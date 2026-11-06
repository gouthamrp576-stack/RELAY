"""Request and response structures for AI text intake."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.donation import (
    DonationDraft,
    FoodCategory,
    QuantityUnit,
    StorageCondition,
)


class TextIntakeRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    donor_name: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=1, max_length=2000)


class ExtractedDonationFields(BaseModel):
    """Fields the model extracts; unknown values remain null."""

    model_config = ConfigDict(extra="forbid")

    food_category: FoodCategory | None
    quantity: str | None
    unit: QuantityUnit | None
    allergens: list[str] | None
    dietary_tags: list[str]
    storage_condition: StorageCondition | None
    pickup_location: str | None
    prepared_at: str | None
    pickup_available_from: str | None
    pickup_deadline: str | None


class TextIntakeResponse(BaseModel):
    status: Literal["needs_information", "needs_review"]
    saved: Literal[False] = False
    donor_confirmation_required: Literal[True] = True
    missing_fields: list[str]
    follow_up_questions: list[str]
    donation: DonationDraft