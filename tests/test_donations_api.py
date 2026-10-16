"""Checks for RELAY's donation-validation API."""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


ENDPOINT = "/api/v1/donations/validate"


@pytest.fixture
def client():
    """Create an isolated API client for each test."""
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def donation_payload():
    """Return a fresh, complete donation draft for each test."""
    return {
        "donor_name": "Demo Event Kitchen",
        "description": "80 vegetarian meals in sealed containers.",
        "food_category": "prepared_meals",
        "quantity": 80,
        "unit": "meals",
        "allergens": ["milk", "wheat"],
        "dietary_tags": ["vegetarian"],
        "storage_condition": "chilled",
        "pickup_location": "Simulated Ottawa pickup location",
        "prepared_at": "2026-10-02T08:00:00-04:00",
        "pickup_available_from": "2026-10-02T10:00:00-04:00",
        "pickup_deadline": "2026-10-02T12:00:00-04:00",
    }


def test_complete_draft_still_requires_confirmation(
    client, donation_payload
):
    response = client.post(ENDPOINT, json=donation_payload)

    assert response.status_code == 200

    result = response.json()
    assert result["status"] == "ready_for_confirmation"
    assert result["missing_fields"] == []
    assert result["donor_confirmation_required"] is True


def test_unknown_allergens_require_more_information(
    client, donation_payload
):
    donation_payload["allergens"] = None

    response = client.post(ENDPOINT, json=donation_payload)

    assert response.status_code == 200

    result = response.json()
    assert result["status"] == "needs_information"
    assert result["missing_fields"] == ["allergens"]
    assert result["donation"]["allergens"] is None


def test_explicit_empty_allergen_list_is_preserved(
    client, donation_payload
):
    donation_payload["allergens"] = []

    response = client.post(ENDPOINT, json=donation_payload)

    assert response.status_code == 200

    result = response.json()
    assert result["status"] == "ready_for_confirmation"
    assert result["donation"]["allergens"] == []
    assert result["donor_confirmation_required"] is True


@pytest.mark.parametrize("quantity", [0, -5, 1.5])
def test_invalid_meal_quantities_are_rejected(
    client, donation_payload, quantity
):
    donation_payload["quantity"] = quantity

    response = client.post(ENDPOINT, json=donation_payload)

    assert response.status_code == 422


def test_deadline_before_pickup_availability_is_rejected(
    client, donation_payload
):
    donation_payload["pickup_deadline"] = (
        "2026-10-02T09:00:00-04:00"
    )

    response = client.post(ENDPOINT, json=donation_payload)

    assert response.status_code == 422


def test_pickup_time_without_timezone_is_rejected(
    client, donation_payload
):
    donation_payload["pickup_available_from"] = (
        "2026-10-02T10:00:00"
    )

    response = client.post(ENDPOINT, json=donation_payload)

    assert response.status_code == 422


def test_client_cannot_add_a_confirmation_flag(
    client, donation_payload
):
    donation_payload["confirmed"] = True

    response = client.post(ENDPOINT, json=donation_payload)

    assert response.status_code == 422