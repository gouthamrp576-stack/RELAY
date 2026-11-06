"""Request and response structures for persistent intake conversations."""

from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
)

from backend.app.schemas.donation_record import DonationRecordResponse


class ConversationCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    donor_name: str = Field(min_length=2, max_length=120)


class ConversationMessage(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    expected_revision: int = Field(strict=True, ge=0)
    message: str = Field(min_length=1, max_length=2000)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    donor_name: str
    revision: int
    donation_id: UUID | None
    latest_result: dict | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConversationSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(strict=True, ge=1)
    acknowledged: StrictBool

    @field_validator("acknowledged")
    @classmethod
    def require_review_acknowledgement(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "Review the preview and explicitly acknowledge saving it."
            )
        return value


class ConversationSaveResponse(BaseModel):
    conversation_id: UUID
    conversation_revision: int
    created: bool
    donation: DonationRecordResponse