"""Database tables for RELAY."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import ForeignKey, Integer, UniqueConstraint

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DonationDraftRecord(Base):
    """A saved draft; saving does not confirm the donation."""
    revision: Mapped[int] = mapped_column(
    Integer,
    nullable=False,
    default=1,
    server_default=text("1"),
    )

    __tablename__ = "donation_drafts"

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_donation_drafts_positive_quantity",
        ),
        CheckConstraint(
            "unit NOT IN ('meals', 'boxes', 'packages') "
            "OR quantity = trunc(quantity)",
            name="ck_donation_drafts_whole_item_quantity",
        ),
        CheckConstraint(
            "pickup_deadline > pickup_available_from",
            name="ck_donation_drafts_pickup_window",
        ),
        CheckConstraint(
            "prepared_at <= pickup_available_from",
            name="ck_donation_drafts_preparation_time",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    donor_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000))

    food_category: Mapped[str | None] = mapped_column(String(30))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(String(20))

    allergens: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )

    dietary_tags: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    storage_condition: Mapped[str | None] = mapped_column(String(20))
    pickup_location: Mapped[str | None] = mapped_column(String(250))

    prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    pickup_available_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    pickup_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    
    
class DonationConfirmationRecord(Base):
    """Record the exact donation details approved in a confirmation."""

    __tablename__ = "donation_confirmations"

    __table_args__ = (
        UniqueConstraint(
            "donation_id",
            "donation_revision",
            name="uq_confirmation_donation_revision",
        ),
        CheckConstraint(
            "donation_revision > 0",
            name="ck_confirmation_positive_revision",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    donation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("donation_drafts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    donation_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    confirmed_by: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    snapshot: Mapped[dict] = mapped_column(
        JSONB(none_as_null=True),
        nullable=False,
    )