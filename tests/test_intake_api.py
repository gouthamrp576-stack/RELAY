"""Test AI intake behavior without calling OpenAI."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.api import intake as intake_api
from backend.app.main import create_app
from backend.app.schemas.intake import ExtractedDonationFields


ENDPOINT = "/api/v1/intake/text"

REQUEST = {
    "donor_name": "Synthetic Test Kitchen",
    "message": "Synthetic donation message for backend testing.",
}


@pytest.fixture
def complete_fields():
    """A simulated, complete extraction returned by the model."""
    return {
        "food_category": "prepared_meals",
        "quantity": "40",
        "unit": "meals",
        "allergens": ["milk", "wheat"],
        "dietary_tags": ["vegetarian"],
        "storage_condition": "chilled",
        "pickup_location": "Synthetic Ottawa pickup location",
        "prepared_at": "2026-10-17T08:00:00-04:00",
        "pickup_available_from": "2026-10-17T10:00:00-04:00",
        "pickup_deadline": "2026-10-17T12:00:00-04:00",
    }


@pytest.fixture
def intake_setup(monkeypatch):
    """Replace credentials and the OpenAI client for every test."""
    provider = MagicMock()

    client_context = MagicMock()
    client_context.__enter__.return_value = provider

    client_factory = MagicMock(return_value=client_context)

    monkeypatch.setattr(intake_api, "OpenAI", client_factory)
    monkeypatch.setattr(
        intake_api,
        "dotenv_values",
        lambda *args, **kwargs: {
            "OPENAI_API_KEY": "test-placeholder-not-a-real-key",
        },
    )

    with TestClient(create_app()) as client:
        yield client, provider, client_factory


def set_model_response(provider, fields):
    provider.responses.parse.return_value = SimpleNamespace(
        status="completed",
        output_parsed=ExtractedDonationFields(**fields),
    )


def test_complete_intake_requires_review(intake_setup, complete_fields):
    client, provider, _ = intake_setup
    set_model_response(provider, complete_fields)

    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "needs_review"
    assert body["saved"] is False
    assert body["donor_confirmation_required"] is True
    assert body["missing_fields"] == []
    assert body["follow_up_questions"] == []
    assert body["donation"]["donor_name"] == REQUEST["donor_name"]
    assert body["donation"]["description"] == REQUEST["message"]
    assert body["donation"]["allergens"] == ["milk", "wheat"]


def test_missing_information_generates_questions(
    intake_setup,
    complete_fields,
):
    client, provider, _ = intake_setup

    missing = {
        "allergens",
        "pickup_location",
        "prepared_at",
        "pickup_available_from",
        "pickup_deadline",
    }

    for field in missing:
        complete_fields[field] = None

    set_model_response(provider, complete_fields)
    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "needs_information"
    assert set(body["missing_fields"]) == missing
    assert len(body["follow_up_questions"]) == len(missing)
    assert all(body["follow_up_questions"])
    assert body["donation"]["allergens"] is None
    assert body["saved"] is False
    assert body["donor_confirmation_required"] is True


def test_explicit_empty_allergen_list_is_preserved(
    intake_setup,
    complete_fields,
):
    client, provider, _ = intake_setup
    complete_fields["allergens"] = []

    set_model_response(provider, complete_fields)
    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 200
    body = response.json()

    assert body["donation"]["allergens"] == []
    assert "allergens" not in body["missing_fields"]
    assert body["status"] == "needs_review"
    assert body["donor_confirmation_required"] is True


@pytest.mark.parametrize("quantity", ["0", "-5", "1.5"])
def test_invalid_meal_quantity_from_ai_is_rejected(
    intake_setup,
    complete_fields,
    quantity,
):
    client, provider, _ = intake_setup
    complete_fields["quantity"] = quantity

    set_model_response(provider, complete_fields)
    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 422
    detail = response.json()["detail"]

    assert detail["issues"]
    assert detail["extracted_fields"]["quantity"] == quantity


def test_timestamp_without_timezone_is_rejected(
    intake_setup,
    complete_fields,
):
    client, provider, _ = intake_setup
    complete_fields["pickup_available_from"] = "2026-10-17T10:00:00"

    set_model_response(provider, complete_fields)
    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 422
    issues = response.json()["detail"]["issues"]

    assert any(
        issue["field"] == "pickup_available_from"
        for issue in issues
    )


def test_deadline_before_pickup_is_rejected(
    intake_setup,
    complete_fields,
):
    client, provider, _ = intake_setup
    complete_fields["pickup_deadline"] = "2026-10-17T09:00:00-04:00"

    set_model_response(provider, complete_fields)
    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 422
    assert response.json()["detail"]["issues"]


def test_missing_api_key_returns_503(intake_setup, monkeypatch):
    client, _, client_factory = intake_setup

    monkeypatch.setattr(
        intake_api,
        "dotenv_values",
        lambda *args, **kwargs: {},
    )

    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 503
    assert response.json()["detail"] == "AI intake is not configured."
    client_factory.assert_not_called()


def test_no_parsed_result_returns_502(intake_setup):
    client, provider, _ = intake_setup

    provider.responses.parse.return_value = SimpleNamespace(
        status="completed",
        output_parsed=None,
    )

    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "The AI did not return a complete extraction."
    )


def test_incomplete_model_response_returns_502(
    intake_setup,
    complete_fields,
):
    client, provider, _ = intake_setup

    provider.responses.parse.return_value = SimpleNamespace(
        status="incomplete",
        output_parsed=ExtractedDonationFields(**complete_fields),
    )

    response = client.post(ENDPOINT, json=REQUEST)

    assert response.status_code == 502


def test_blank_message_is_rejected_before_ai_call(intake_setup):
    client, _, client_factory = intake_setup

    response = client.post(
        ENDPOINT,
        json={
            "donor_name": "Synthetic Test Kitchen",
            "message": "   ",
        },
    )

    assert response.status_code == 422
    client_factory.assert_not_called()


def test_client_cannot_add_confirmation_flag(intake_setup):
    client, _, client_factory = intake_setup

    response = client.post(
        ENDPOINT,
        json={
            **REQUEST,
            "confirmed": True,
        },
    )

    assert response.status_code == 422
    client_factory.assert_not_called()