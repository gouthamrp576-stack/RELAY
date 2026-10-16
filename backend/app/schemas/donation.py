"""Donation draft structure and validation rules."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


FoodCategory = Literal["prepared_meals", "packaged_food"]
QuantityUnit = Literal["meals", "boxes", "packages", "kg"]
StorageCondition = Literal["ambient", "chilled", "frozen", "hot"]

Label = Annotated[str, Field(min_length=1, max_length=60)]


class DonationDraft(BaseModel):
    """Information entered by a donor or suggested by AI."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    donor_name: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=1, max_length=2000)

    food_category: FoodCategory | None = None

    quantity: Decimal | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        max_digits=12,
        decimal_places=3,
    )
    unit: QuantityUnit | None = None

    allergens: list[Label] | None = None
    dietary_tags: list[Label] = Field(default_factory=list)
    storage_condition: StorageCondition | None = None

    pickup_location: str | None = Field(
        default=None,
        min_length=3,
        max_length=250,
    )

    prepared_at: AwareDatetime | None = None
    pickup_available_from: AwareDatetime | None = None
    pickup_deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        """Reject contradictory quantities and times."""

        if self.quantity is not None:
            if self.unit in {"meals", "boxes", "packages"}:
                if self.quantity != self.quantity.to_integral_value():
                    raise ValueError(
                        "Meals, boxes, and packages require whole-number quantities."
                    )

        if (
            self.pickup_available_from is not None
            and self.pickup_deadline is not None
        ):
            if self.pickup_deadline <= self.pickup_available_from:
                raise ValueError(
                    "Pickup deadline must be after pickup availability."
                )

        if (
            self.prepared_at is not None
            and self.pickup_available_from is not None
        ):
            if self.prepared_at > self.pickup_available_from:
                raise ValueError(
                    "Preparation time cannot follow pickup availability."
                )

        return self

    def missing_required_fields(self) -> list[str]:
        """Identify information needed before donor confirmation."""

        required = [
            "food_category",
            "quantity",
            "unit",
            "allergens",
            "storage_condition",
            "pickup_location",
            "pickup_available_from",
            "pickup_deadline",
        ]

        if self.food_category == "prepared_meals":
            required.append("prepared_at")

        return [
            field
            for field in required
            if getattr(self, field) is None
        ]
    
class DonationValidationResult(BaseModel):
    """Result of checking a donation draft."""

    status: Literal["needs_information", "ready_for_confirmation"]
    missing_fields: list[str]
    donor_confirmation_required: Literal[True] = True
    donation: DonationDraft