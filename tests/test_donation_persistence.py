"""Integration tests using the separate relay_test PostgreSQL database."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from backend.app.database import get_engine, get_session
from backend.app.main import create_app
from backend.app.models import DonationDraftRecord

from datetime import datetime, timedelta, timezone

from backend.app.models import DonationConfirmationRecord

@pytest.fixture(scope="module")
def test_engine():
    # Reuse connection settings, but explicitly select the test database.
    test_url = get_engine().url.set(database="relay_test")

    engine = create_engine(
        test_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_connection(test_engine):
    # Each test gets an outer transaction that is rolled back afterwards.
    with test_engine.connect() as connection:
        transaction = connection.begin()

        try:
            database_name = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()

            assert database_name == "relay_test"

            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
def client(db_connection):
    application = create_app()

    def override_session():
        # API commits release savepoints, leaving the outer test
        # transaction available for rollback.
        with Session(
            bind=db_connection,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session

    application.dependency_overrides[get_session] = override_session

    try:
        with TestClient(application) as test_client:
            yield test_client
    finally:
        application.dependency_overrides.clear()


@pytest.fixture
def donation_payload():
    return {
        "donor_name": "Synthetic Test Kitchen",
        "description": "80 vegetarian meals in sealed containers.",
        "food_category": "prepared_meals",
        "quantity": 80,
        "unit": "meals",
        "allergens": ["milk", "wheat"],
        "dietary_tags": ["vegetarian"],
        "storage_condition": "chilled",
        "pickup_location": "Synthetic pickup location",
        "prepared_at": "2026-10-08T08:00:00-04:00",
        "pickup_available_from": "2026-10-08T10:00:00-04:00",
        "pickup_deadline": "2026-10-08T12:00:00-04:00",
    }


def save_draft(client, payload):
    response = client.post("/api/v1/donations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def count_drafts(connection):
    statement = select(func.count()).select_from(DonationDraftRecord)
    return connection.execute(statement).scalar_one()


def test_create_stores_row_and_get_returns_it(
    client, db_connection, donation_payload
):
    saved = save_draft(client, donation_payload)

    table = DonationDraftRecord.__table__
    row = db_connection.execute(
        select(table).where(table.c.id == UUID(saved["id"]))
    ).mappings().one()

    assert row["quantity"] == Decimal("80")
    assert row["donor_name"] == donation_payload["donor_name"]

    response = client.get(f"/api/v1/donations/{saved['id']}")

    assert response.status_code == 200
    assert response.json() == saved
    assert saved["status"] == "ready_for_confirmation"
    assert saved["donor_confirmation_required"] is True


@pytest.mark.parametrize(
    ("allergens", "expected_status"),
    [
        (None, "needs_information"),
        ([], "ready_for_confirmation"),
    ],
)
def test_allergen_meaning_survives_storage(
    client, donation_payload, allergens, expected_status
):
    donation_payload["allergens"] = allergens
    saved = save_draft(client, donation_payload)

    response = client.get(f"/api/v1/donations/{saved['id']}")

    assert response.status_code == 200
    retrieved = response.json()

    assert retrieved["donation"]["allergens"] == allergens
    assert retrieved["status"] == expected_status
    assert retrieved["missing_fields"] == (
        ["allergens"] if allergens is None else []
    )
    assert retrieved["donor_confirmation_required"] is True


def test_update_changes_existing_row_without_creating_another(
    client, db_connection, donation_payload
):
    saved = save_draft(client, donation_payload)
    count_before = count_drafts(db_connection)

    donation_payload["quantity"] = 75
    donation_payload["description"] = "75 vegetarian meals."
    donation_payload["allergens"] = None

    response = client.put(
        f"/api/v1/donations/{saved['id']}",
        json=donation_payload,
    )

    assert response.status_code == 200
    updated = response.json()

    assert updated["id"] == saved["id"]
    assert updated["created_at"] == saved["created_at"]
    assert Decimal(updated["donation"]["quantity"]) == Decimal("75")
    assert updated["donation"]["allergens"] is None
    assert updated["status"] == "needs_information"
    assert updated["missing_fields"] == ["allergens"]
    assert updated["donor_confirmation_required"] is True
    assert count_drafts(db_connection) == count_before

    retrieved = client.get(f"/api/v1/donations/{saved['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == updated


@pytest.mark.parametrize("invalid_quantity", [0, -5, 1.5])
def test_invalid_update_leaves_saved_draft_unchanged(
    client, donation_payload, invalid_quantity
):
    saved = save_draft(client, donation_payload)

    donation_payload["quantity"] = invalid_quantity

    response = client.put(
        f"/api/v1/donations/{saved['id']}",
        json=donation_payload,
    )

    assert response.status_code == 422

    retrieved = client.get(f"/api/v1/donations/{saved['id']}")

    assert retrieved.status_code == 200
    assert retrieved.json() == saved


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_unknown_id_returns_404(client, donation_payload, method):
    url = f"/api/v1/donations/{uuid4()}"

    if method == "GET":
        response = client.get(url)
    else:
        response = client.put(url, json=donation_payload)

    assert response.status_code == 404
    assert response.json()["detail"] == "Donation draft not found."


def test_invalid_create_does_not_insert_a_row(
    client, db_connection, donation_payload
):
    count_before = count_drafts(db_connection)
    donation_payload["quantity"] = -5

    response = client.post("/api/v1/donations", json=donation_payload)

    assert response.status_code == 422
    assert count_drafts(db_connection) == count_before


def test_client_cannot_confirm_when_creating(
    client, db_connection, donation_payload
):
    count_before = count_drafts(db_connection)
    donation_payload["confirmed"] = True

    response = client.post("/api/v1/donations", json=donation_payload)

    assert response.status_code == 422
    assert count_drafts(db_connection) == count_before
    
    
@pytest.fixture
def fresh_donation_payload(donation_payload):
    """Create a complete donation with a future pickup deadline."""
    now = datetime.now(timezone.utc).replace(microsecond=0)

    donation_payload["prepared_at"] = (
        now - timedelta(hours=1)
    ).isoformat()
    donation_payload["pickup_available_from"] = (
        now + timedelta(hours=1)
    ).isoformat()
    donation_payload["pickup_deadline"] = (
        now + timedelta(hours=3)
    ).isoformat()

    return donation_payload


def confirmation_body(saved):
    return {
        "expected_revision": saved["revision"],
        "confirmed_by": "Synthetic Test Donor",
        "acknowledged": True,
    }


def count_confirmations(connection, donation_id):
    statement = (
        select(func.count())
        .select_from(DonationConfirmationRecord)
        .where(
            DonationConfirmationRecord.donation_id
            == UUID(donation_id)
        )
    )
    return connection.execute(statement).scalar_one()


def test_complete_draft_confirmation_stores_snapshot(
    client, db_connection, fresh_donation_payload
):
    saved = save_draft(client, fresh_donation_payload)

    response = client.post(
        f"/api/v1/donations/{saved['id']}/confirm",
        json=confirmation_body(saved),
    )

    assert response.status_code == 200, response.text
    confirmed = response.json()

    assert confirmed["status"] == "confirmed"
    assert confirmed["donor_confirmation_required"] is False
    assert confirmed["missing_fields"] == []
    assert confirmed["confirmation"]["donation_revision"] == saved["revision"]
    assert confirmed["confirmation"]["confirmed_by"] == "Synthetic Test Donor"

    table = DonationConfirmationRecord.__table__
    row = db_connection.execute(
        select(table).where(
            table.c.donation_id == UUID(saved["id"])
        )
    ).mappings().one()

    assert str(row["id"]) == confirmed["confirmation"]["id"]
    assert row["donation_revision"] == saved["revision"]
    assert row["snapshot"] == saved["donation"]

    retrieved = client.get(f"/api/v1/donations/{saved['id']}")

    assert retrieved.status_code == 200
    assert retrieved.json() == confirmed


@pytest.mark.parametrize(
    "missing_field",
    ["allergens", "quantity", "prepared_at"],
)
def test_incomplete_draft_cannot_be_confirmed(
    client, db_connection, fresh_donation_payload, missing_field
):
    fresh_donation_payload[missing_field] = None
    saved = save_draft(client, fresh_donation_payload)

    response = client.post(
        f"/api/v1/donations/{saved['id']}/confirm",
        json=confirmation_body(saved),
    )

    assert response.status_code == 409, response.text
    assert missing_field in response.json()["detail"]["missing_fields"]
    assert count_confirmations(db_connection, saved["id"]) == 0

    retrieved = client.get(f"/api/v1/donations/{saved['id']}")

    assert retrieved.status_code == 200
    assert retrieved.json() == saved


def test_outdated_revision_cannot_be_confirmed(
    client, db_connection, fresh_donation_payload
):
    saved = save_draft(client, fresh_donation_payload)

    updated_payload = {
        **fresh_donation_payload,
        "quantity": 75,
        "description": "75 vegetarian meals.",
    }

    update_response = client.put(
        f"/api/v1/donations/{saved['id']}",
        json=updated_payload,
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["revision"] == saved["revision"] + 1

    response = client.post(
        f"/api/v1/donations/{saved['id']}/confirm",
        json=confirmation_body(saved),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["current_revision"] == updated["revision"]
    assert count_confirmations(db_connection, saved["id"]) == 0


@pytest.mark.parametrize("acknowledgement", [False, "true", 1])
def test_confirmation_requires_explicit_boolean_acknowledgement(
    client, db_connection, fresh_donation_payload, acknowledgement
):
    saved = save_draft(client, fresh_donation_payload)
    request_body = confirmation_body(saved)
    request_body["acknowledged"] = acknowledgement

    response = client.post(
        f"/api/v1/donations/{saved['id']}/confirm",
        json=request_body,
    )

    assert response.status_code == 422, response.text
    assert count_confirmations(db_connection, saved["id"]) == 0


def test_repeated_confirmation_returns_existing_record(
    client, db_connection, fresh_donation_payload
):
    saved = save_draft(client, fresh_donation_payload)
    url = f"/api/v1/donations/{saved['id']}/confirm"
    request_body = confirmation_body(saved)

    first = client.post(url, json=request_body)
    second = client.post(url, json=request_body)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert count_confirmations(db_connection, saved["id"]) == 1


def test_confirmed_donation_cannot_be_edited(
    client, db_connection, fresh_donation_payload
):
    saved = save_draft(client, fresh_donation_payload)

    confirmation_response = client.post(
        f"/api/v1/donations/{saved['id']}/confirm",
        json=confirmation_body(saved),
    )

    assert confirmation_response.status_code == 200
    confirmed = confirmation_response.json()

    changed_payload = {
        **fresh_donation_payload,
        "quantity": 10,
        "description": "10 vegetarian meals.",
    }

    update_response = client.put(
        f"/api/v1/donations/{saved['id']}",
        json=changed_payload,
    )

    assert update_response.status_code == 409, update_response.text

    retrieved = client.get(f"/api/v1/donations/{saved['id']}")

    assert retrieved.status_code == 200
    assert retrieved.json() == confirmed
    assert count_confirmations(db_connection, saved["id"]) == 1


def test_expired_draft_cannot_be_confirmed(
    client, db_connection, fresh_donation_payload
):
    now = datetime.now(timezone.utc).replace(microsecond=0)

    fresh_donation_payload["prepared_at"] = (
        now - timedelta(hours=3)
    ).isoformat()
    fresh_donation_payload["pickup_available_from"] = (
        now - timedelta(hours=2)
    ).isoformat()
    fresh_donation_payload["pickup_deadline"] = (
        now - timedelta(hours=1)
    ).isoformat()

    saved = save_draft(client, fresh_donation_payload)

    response = client.post(
        f"/api/v1/donations/{saved['id']}/confirm",
        json=confirmation_body(saved),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "The pickup deadline has already passed."
    )
    assert count_confirmations(db_connection, saved["id"]) == 0


def test_confirming_unknown_donation_returns_404(client):
    response = client.post(
        f"/api/v1/donations/{uuid4()}/confirm",
        json={
            "expected_revision": 1,
            "confirmed_by": "Synthetic Test Donor",
            "acknowledged": True,
        },
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Donation draft not found."