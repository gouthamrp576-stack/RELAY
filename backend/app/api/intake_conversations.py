"""Persistent intake conversations and reviewed-preview saving."""

import asyncio
from copy import deepcopy
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from backend.app.api.donations import (
    build_saved_response,
    get_record_or_404,
)
from backend.app.database import get_session
from backend.app.intake_agent import IntakeAgentError, run_intake_turn
from backend.app.models import (
    DonationDraftRecord,
    IntakeConversationRecord,
)
from backend.app.schemas.donation import DonationDraft
from backend.app.schemas.intake_conversation import (
    ConversationCreate,
    ConversationMessage,
    ConversationResponse,
    ConversationSaveRequest,
    ConversationSaveResponse,
)


router = APIRouter(
    prefix="/intake/conversations",
    tags=["Intake Conversations"],
)

DatabaseSession = Annotated[Session, Depends(get_session)]


def get_conversation(
    session: Session,
    conversation_id: UUID,
    *,
    lock: bool = False,
) -> IntakeConversationRecord:
    record = session.get(
        IntakeConversationRecord,
        conversation_id,
        with_for_update=True if lock else None,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Intake conversation not found.",
        )

    return record


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=201,
    summary="Start an intake conversation",
)
def create_conversation(
    request: ConversationCreate,
    session: DatabaseSession,
) -> ConversationResponse:
    with session.begin():
        record = IntakeConversationRecord(
            donor_name=request.donor_name,
        )
        session.add(record)
        session.flush()
        session.refresh(record)

        response = ConversationResponse.model_validate(record)

    return response


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Retrieve an intake conversation",
    responses={404: {"description": "Conversation not found"}},
)
def read_conversation(
    conversation_id: UUID,
    session: DatabaseSession,
) -> ConversationResponse:
    record = get_conversation(session, conversation_id)
    return ConversationResponse.model_validate(record)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationResponse,
    summary="Continue an intake conversation",
    responses={
        404: {"description": "Conversation not found"},
        409: {"description": "Revision conflict or already saved"},
        502: {"description": "Agent or upstream response failed"},
        503: {"description": "AI service unavailable or not configured"},
        504: {"description": "Agent timed out"},
    },
)
def send_conversation_message(
    conversation_id: UUID,
    request: ConversationMessage,
    session: DatabaseSession,
) -> ConversationResponse:
    with session.begin():
        record = get_conversation(session, conversation_id)

        if record.donation_id is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "This conversation has already been saved as a "
                        "donation draft. Use the donation workflow for "
                        "further changes."
                    ),
                    "donation_id": str(record.donation_id),
                },
            )

        if record.revision != request.expected_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "The conversation has changed. Retrieve its current "
                        "state before sending another message."
                    ),
                    "current_revision": record.revision,
                },
            )

        donor_name = record.donor_name
        history = deepcopy(record.history)
        original_revision = record.revision

    try:
        turn = asyncio.run(
            run_intake_turn(
                donor_name=donor_name,
                history=history,
                message=request.message,
            )
        )
    except IntakeAgentError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from None

    with session.begin():
        statement = (
            update(IntakeConversationRecord)
            .where(
                IntakeConversationRecord.id == conversation_id,
                IntakeConversationRecord.revision == original_revision,
                IntakeConversationRecord.donation_id.is_(None),
            )
            .values(
                history=turn.history,
                latest_result=turn.latest_result,
                revision=original_revision + 1,
                updated_at=func.now(),
            )
            .returning(IntakeConversationRecord.id)
            .execution_options(synchronize_session=False)
        )

        updated_id = session.execute(statement).scalar_one_or_none()

        if updated_id is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Another request changed or saved the conversation "
                    "while the agent was running. This result was not saved. "
                    "Retrieve the conversation before continuing."
                ),
            )

        session.expire_all()
        updated_record = get_conversation(session, conversation_id)
        response = ConversationResponse.model_validate(updated_record)

    return response


@router.post(
    "/{conversation_id}/save-draft",
    response_model=ConversationSaveResponse,
    summary="Save the reviewed conversation preview as a donation draft",
    responses={
        404: {"description": "Conversation not found"},
        409: {"description": "Stale revision or incomplete/invalid preview"},
    },
)
def save_conversation_draft(
    conversation_id: UUID,
    request: ConversationSaveRequest,
    session: DatabaseSession,
) -> ConversationSaveResponse:
    with session.begin():
        # Serialize save requests for this conversation.
        record = get_conversation(
            session,
            conversation_id,
            lock=True,
        )

        if record.revision != request.expected_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "The conversation has changed. Retrieve and review "
                        "the current preview before saving."
                    ),
                    "current_revision": record.revision,
                },
            )

        if record.donation_id is not None:
            # A repeated request returns the existing linked draft.
            draft = get_record_or_404(
                session,
                record.donation_id,
                lock=True,
            )
            response = ConversationSaveResponse(
                conversation_id=record.id,
                conversation_revision=record.revision,
                created=False,
                donation=build_saved_response(draft, session),
            )
        else:
            preview = record.latest_result or {}

            if preview.get("status") != "needs_review":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "Complete and review a valid donation preview "
                            "before saving."
                        ),
                        "missing_fields": preview.get("missing_fields", []),
                    },
                )

            # Validate the stored proposal again at the save boundary.
            try:
                donation = DonationDraft.model_validate(
                    preview.get("donation")
                )
            except ValidationError:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The stored preview is invalid. Correct it in the "
                        "conversation before saving."
                    ),
                ) from None

            missing = donation.missing_required_fields()

            if missing:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Complete the missing information first.",
                        "missing_fields": missing,
                    },
                )

            if donation.donor_name != record.donor_name:
                raise HTTPException(
                    status_code=409,
                    detail="The preview donor does not match the conversation.",
                )

            draft = DonationDraftRecord(**donation.model_dump())
            session.add(draft)
            session.flush()
            session.refresh(draft)

            # Create the draft and its conversation link atomically.
            record.donation_id = draft.id
            session.flush()

            response = ConversationSaveResponse(
                conversation_id=record.id,
                conversation_revision=record.revision,
                created=True,
                donation=build_saved_response(draft, session),
            )

    return response