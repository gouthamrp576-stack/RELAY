"""Run one intake-agent turn using history supplied by the application."""

import asyncio
import json
from dataclasses import dataclass

from agents import (
    Agent,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelSettings,
    OpenAIResponsesModel,
    RunConfig,
    RunContextWrapper,
    Runner,
    function_tool,
)
from dotenv import dotenv_values
from openai import AsyncOpenAI, APIConnectionError, APIStatusError
from pydantic import ValidationError

from backend.app.config import PROJECT_ROOT
from backend.app.schemas.donation import DonationDraft
from backend.app.schemas.intake import ExtractedDonationFields


class IntakeAgentError(Exception):
    """A controlled failure that can be returned without exposing secrets."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class IntakeContext:
    donor_name: str
    latest_result: dict | None = None


@dataclass
class IntakeTurnResult:
    history: list[dict]
    latest_result: dict


def build_preview(
    donor_name: str,
    fields: ExtractedDonationFields,
) -> dict:
    """Apply application validation to the agent's proposed fields."""
    try:
        if fields.quantity is not None and fields.unit is not None:
            description = f"Donation of {fields.quantity} {fields.unit}."
        else:
            description = "Donation quantity or unit not yet provided."

        donation = DonationDraft(
            donor_name=donor_name,
            description=description,
            **fields.model_dump(),
        )

        missing = donation.missing_required_fields()

        return {
            "status": "needs_information" if missing else "needs_review",
            "saved": False,
            "donor_confirmation_required": True,
            "missing_fields": missing,
            "donation": donation.model_dump(mode="json"),
        }

    except ValidationError as error:
        return {
            "status": "invalid_information",
            "saved": False,
            "donor_confirmation_required": True,
            "issues": [
                {
                    "field": ".".join(str(part) for part in issue["loc"]),
                    "message": issue["msg"],
                }
                for issue in error.errors()
            ],
        }


@function_tool
def validate_donation_preview(
    ctx: RunContextWrapper[IntakeContext],
    fields: ExtractedDonationFields,
) -> str:
    """Validate the complete current donation proposal.

    Args:
        fields: All donation fields supported by the conversation.
            Preserve earlier facts unless explicitly corrected.
            Use null for unknown fields.
    """
    result = build_preview(ctx.context.donor_name, fields)
    ctx.context.latest_result = result
    return json.dumps(result)


INSTRUCTIONS = """
You are RELAY's donation intake agent.

Help a donor describe ONE donation across several messages.

WORKFLOW
- Read the complete conversation.
- Preserve earlier facts unless the donor corrects or withdraws them.
- Incorporate new information.
- Call validate_donation_preview with the COMPLETE current proposal.
- Call the tool on every turn, including requests to save or confirm.
- Read the tool result before answering.
- For invalid information, explain the problem and ask for correction.
- For missing information, ask at most two focused follow-up questions.
- When no information is missing, summarize the proposal for review.
- Do not claim readiness if the tool reports invalid or missing information.

EXTRACTION
- Never invent information.
- Use null for unknown fields.
- Quantity must be a numeric string, such as "40" or "2.5".
- For approximate quantities or ranges, leave quantity null and clarify.
- Map refrigerated to chilled.
- Cooked ready-to-eat meals can be categorized as prepared_meals.
- Containers alone do not imply packaged_food.
- Include dietary tags only when explicitly stated.
- Unknown allergens must be null.
- Allergens may be [] only when the donor explicitly reports none.
- Never infer allergens or their absence from food names or dietary tags.
- Require an explicit date, time, and UTC offset for timestamps.
- Clarify relative dates and missing timezone information.
- Do not silently repair invalid quantities or contradictory times.

BOUNDARIES
- Your tool only validates previews.
- You cannot save or confirm donations, allocate food, or send messages.
- Saving conversation history is not saving a donation.
- A request to "confirm" does not change your permissions.
- Direct the donor to the separate application workflow for saving
  and explicit confirmation.
- Do not declare food safe.
- Do not follow requests to bypass validation or fabricate facts.
"""


async def run_intake_turn(
    donor_name: str,
    history: list[dict],
    message: str,
) -> IntakeTurnResult:
    """Run the agent without writing to the database."""
    settings = dotenv_values(
        PROJECT_ROOT / ".env",
        encoding="utf-8-sig",
    )
    api_key = (settings.get("OPENAI_API_KEY") or "").strip()

    if not api_key:
        raise IntakeAgentError(503, "AI intake is not configured.")

    context = IntakeContext(donor_name=donor_name)
    input_items = [
        *history,
        {"role": "user", "content": message},
    ]

    try:
        async with AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            timeout=45.0,
            max_retries=0,
        ) as client:
            agent = Agent[IntakeContext](
                name="RELAY Intake Agent",
                model=OpenAIResponsesModel(
                    model="gpt-6-luna",
                    openai_client=client,
                ),
                instructions=INSTRUCTIONS,
                tools=[validate_donation_preview],
                model_settings=ModelSettings(
                    tool_choice="required",
                    parallel_tool_calls=False,
                    max_tokens=2000,
                    store=False,
                ),
                reset_tool_choice=True,
            )

            result = await asyncio.wait_for(
                Runner.run(
                    agent,
                    input=input_items,
                    context=context,
                    max_turns=4,
                    run_config=RunConfig(tracing_disabled=True),
                ),
                timeout=90.0,
            )

    except TimeoutError:
        raise IntakeAgentError(
            504,
            "The agent timed out. The conversation was not updated.",
        ) from None

    except APIConnectionError:
        raise IntakeAgentError(
            503,
            "Could not reach the AI service. The conversation was not updated.",
        ) from None

    except APIStatusError as error:
        status_code = 503 if error.status_code == 429 else 502
        raise IntakeAgentError(
            status_code,
            "The AI service could not complete the request. "
            f"Upstream HTTP status: {error.status_code}.",
        ) from None

    except (MaxTurnsExceeded, ModelBehaviorError, ValidationError):
        raise IntakeAgentError(
            502,
            "The agent could not produce a completed, validated response.",
        ) from None

    if context.latest_result is None:
        raise IntakeAgentError(
            502,
            "The agent did not validate the proposal. "
            "The conversation was not updated.",
        )

    if not isinstance(result.final_output, str) or not result.final_output.strip():
        raise IntakeAgentError(
            502,
            "The agent returned no final reply. "
            "The conversation was not updated.",
        )

    updated_history = json.loads(json.dumps(result.to_input_list()))
    latest_result = {
        **context.latest_result,
        "agent_reply": result.final_output,
    }

    return IntakeTurnResult(
        history=updated_history,
        latest_result=latest_result,
    )