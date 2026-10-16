"""Validate, store, update, and confirm donation drafts."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_session
from backend.app.models import (
    DonationConfirmationRecord,
    DonationDraftRecord,
)
from backend.app.schemas.confirmation import (
    DonationConfirmationRequest,
    DonationConfirmationSummary,
)
from backend.app.schemas.donation import (
    DonationDraft,
    DonationValidationResult,
)
from backend.app.schemas.donation_record import DonationRecordResponse


router = APIRouter(prefix="/donations", tags=["Donations"])

DatabaseSession = Annotated[Session, Depends(get_session)]


def get_record_or_404(
    session: Session,
    donation_id: UUID,
    *,
    lock: bool = False,
) -> DonationDraftRecord:
    record = session.get(
        DonationDraftRecord,
        donation_id,
        with_for_update=True if lock else None,
    )

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Donation draft not found.",
        )

    return record


def find_confirmation(
    session: Session,
    record: DonationDraftRecord,
) -> DonationConfirmationRecord | None:
    statement = select(DonationConfirmationRecord).where(
        DonationConfirmationRecord.donation_id == record.id,
        DonationConfirmationRecord.donation_revision == record.revision,
    )

    return session.execute(statement).scalar_one_or_none()


def build_saved_response(
    record: DonationDraftRecord,
    session: Session,
) -> DonationRecordResponse:
    donation = DonationDraft.model_validate(
        record,
        from_attributes=True,
    )
    missing_fields = donation.missing_required_fields()
    confirmation = find_confirmation(session, record)

    confirmation_summary = None

    if confirmation is not None:
        response_status = "confirmed"
        confirmation_summary = DonationConfirmationSummary(
            id=confirmation.id,
            donation_revision=confirmation.donation_revision,
            confirmed_by=confirmation.confirmed_by,
            confirmed_at=confirmation.confirmed_at,
        )
    elif missing_fields:
        response_status = "needs_information"
    else:
        response_status = "ready_for_confirmation"

    return DonationRecordResponse(
        id=record.id,
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
        status=response_status,
        missing_fields=missing_fields,
        donor_confirmation_required=confirmation is None,
        confirmation=confirmation_summary,
        donation=donation,
    )


@router.post(
    "/validate",
    response_model=DonationValidationResult,
    summary="Validate a donation draft",
)
def validate_donation(
    donation: DonationDraft,
) -> DonationValidationResult:
    """Check completeness without saving or confirming."""
    missing_fields = donation.missing_required_fields()

    return DonationValidationResult(
        status=(
            "needs_information"
            if missing_fields
            else "ready_for_confirmation"
        ),
        missing_fields=missing_fields,
        donation=donation,
    )


@router.post(
    "",
    response_model=DonationRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a donation draft",
)
def create_donation(
    donation: DonationDraft,
    session: DatabaseSession,
) -> DonationRecordResponse:
    """Save a draft without confirming it."""
    with session.begin():
        record = DonationDraftRecord(**donation.model_dump())
        session.add(record)
        session.flush()
        session.refresh(record)

        result = build_saved_response(record, session)

    return result


@router.get(
    "/{donation_id}",
    response_model=DonationRecordResponse,
    summary="Retrieve a donation draft",
)
def get_donation(
    donation_id: UUID,
    session: DatabaseSession,
) -> DonationRecordResponse:
    """Retrieve stored information and confirmation state."""
    record = get_record_or_404(session, donation_id)
    return build_saved_response(record, session)


@router.put(
    "/{donation_id}",
    response_model=DonationRecordResponse,
    summary="Update a donation draft",
)
def update_donation(
    donation_id: UUID,
    donation: DonationDraft,
    session: DatabaseSession,
) -> DonationRecordResponse:
    """Replace an unconfirmed draft and advance its revision."""
    with session.begin():
        record = get_record_or_404(
            session,
            donation_id,
            lock=True,
        )

        if find_confirmation(session, record) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This donation is confirmed. "
                    "Confirmed donations cannot be edited "
                    "through the draft-update endpoint."
                ),
            )

        for field_name, value in donation.model_dump().items():
            setattr(record, field_name, value)

        record.revision += 1

        session.flush()
        session.refresh(record)

        result = build_saved_response(record, session)

    return result


@router.post(
    "/{donation_id}/confirm",
    response_model=DonationRecordResponse,
    summary="Confirm a complete donation draft",
)
def confirm_donation(
    donation_id: UUID,
    confirmation_request: DonationConfirmationRequest,
    session: DatabaseSession,
) -> DonationRecordResponse:
    """Record explicit confirmation of the reviewed draft revision."""
    with session.begin():
        record = get_record_or_404(
            session,
            donation_id,
            lock=True,
        )

        if confirmation_request.expected_revision != record.revision:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        "The draft revision has changed. "
                        "Retrieve and review the current draft."
                    ),
                    "current_revision": record.revision,
                },
            )

        existing_confirmation = find_confirmation(session, record)

        if existing_confirmation is not None:
            # A repeated request returns the original confirmation.
            result = build_saved_response(record, session)
        else:
            donation = DonationDraft.model_validate(
                record,
                from_attributes=True,
            )
            missing_fields = donation.missing_required_fields()

            if missing_fields:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": (
                            "Complete the missing information "
                            "before confirming."
                        ),
                        "missing_fields": missing_fields,
                    },
                )

            if (
                donation.pickup_deadline is not None
                and donation.pickup_deadline <= datetime.now(timezone.utc)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The pickup deadline has already passed.",
                )

            confirmation = DonationConfirmationRecord(
                donation_id=record.id,
                donation_revision=record.revision,
                confirmed_by=confirmation_request.confirmed_by,
                snapshot=donation.model_dump(mode="json"),
            )

            session.add(confirmation)
            session.flush()

            result = build_saved_response(record, session)

    return result