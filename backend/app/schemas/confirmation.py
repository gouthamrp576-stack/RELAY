"""Request and response schemas for donor confirmation."""

from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
)


class DonationConfirmationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    expected_revision: int = Field(strict=True, ge=1)
    confirmed_by: str = Field(min_length=2, max_length=120)
    acknowledged: StrictBool

    @field_validator("acknowledged")
    @classmethod
    def require_acknowledgement(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "You must explicitly acknowledge the donation details."
            )
        return value


class DonationConfirmationSummary(BaseModel):
    id: UUID
    donation_revision: int
    confirmed_by: str
    confirmed_at: AwareDatetime