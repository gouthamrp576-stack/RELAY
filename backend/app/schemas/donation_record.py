"""API response for a stored donation and its confirmation state."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from backend.app.schemas.confirmation import (
    DonationConfirmationSummary,
)
from backend.app.schemas.donation import DonationValidationResult


class DonationRecordResponse(DonationValidationResult):
    id: UUID
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    status: Literal[
        "needs_information",
        "ready_for_confirmation",
        "confirmed",
    ]

    donor_confirmation_required: bool = True
    confirmation: DonationConfirmationSummary | None = None