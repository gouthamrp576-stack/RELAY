"""Conversation API tests using PostgreSQL and a simulated agent."""

from contextlib import contextmanager
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.orm import Session

from backend.app.api import intake_conversations as conversation_api
from backend.app.database import get_engine, get_session
from backend.app.intake_agent import (
    IntakeAgentError,
    IntakeTurnResult,
    build_preview,
)
from backend.app.main import create_app
from backend.app.models import (
    DonationConfirmationRecord,
    DonationDraftRecord,
    IntakeConversationRecord,
)
from backend.app.schemas.intake import ExtractedDonationFields

from copy import deepcopy

from fastapi import HTTPException

from backend.app.schemas.donation import DonationDraft


BASE_URL = "/api/v1/intake/conversations"


@pytest.fixture(scope="module")
def test_engine():
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
def client_factory(db_connection):
    @contextmanager
    def open_client():
        application = create_app()

        def override_session():
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

    return open_client


@pytest.fixture
def client(client_factory):
    with client_factory() as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def agent_mock(monkeypatch):
    # Every test replaces the agent. Unexpected calls fail immediately.
    mock = AsyncMock(
        side_effect=AssertionError("Unexpected agent call in this test.")
    )
    monkeypatch.setattr(conversation_api, "run_intake_turn", mock)
    return mock


def create_conversation(client, donor_name="Synthetic Test Kitchen"):
    response = client.post(
        BASE_URL,
        json={"donor_name": donor_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def send_message(client, conversation_id, revision, message):
    return client.post(
        f"{BASE_URL}/{conversation_id}/messages",
        json={
            "expected_revision": revision,
            "message": message,
        },
    )


def read_row(connection, conversation_id):
    table = IntakeConversationRecord.__table__
    statement = select(table).where(
        table.c.id == UUID(conversation_id)
    )
    return dict(connection.execute(statement).mappings().one())


def make_turn(
    history,
    message,
    quantity="40",
    complete=False,
    donor_name="Synthetic Test Kitchen",
):
    """Build a simulated agent result using the real preview validator."""
    fields = ExtractedDonationFields(
        food_category="prepared_meals",
        quantity=quantity,
        unit="meals",
        allergens=["milk", "wheat"] if complete else None,
        dietary_tags=["vegetarian"],
        storage_condition="chilled",
        pickup_location="Synthetic Ottawa pickup" if complete else None,
        prepared_at="2026-11-07T08:00:00-05:00" if complete else None,
        pickup_available_from=(
            "2026-11-07T10:00:00-05:00" if complete else None
        ),
        pickup_deadline=(
            "2026-11-07T12:00:00-05:00" if complete else None
        ),
    )

    latest_result = build_preview(donor_name, fields)
    latest_result["agent_reply"] = "Synthetic agent reply."

    return IntakeTurnResult(
        history=[
            *history,
            {"role": "user", "content": message},
            {"role": "assistant", "content": "Synthetic agent reply."},
        ],
        latest_result=latest_result,
    )


def prepare_agent(agent_mock, turn):
    agent_mock.side_effect = None
    agent_mock.return_value = turn


def test_create_stores_empty_conversation(
    client,
    db_connection,
    agent_mock,
):
    created = create_conversation(client)
    row = read_row(db_connection, created["id"])

    assert created["revision"] == 0
    assert created["latest_result"] is None
    assert row["history"] == []
    assert row["latest_result"] is None
    assert row["donor_name"] == "Synthetic Test Kitchen"
    agent_mock.assert_not_awaited()


def test_message_persists_and_new_app_can_retrieve_it(
    client_factory,
    db_connection,
    agent_mock,
):
    message = "We have 40 vegetarian meals."
    turn = make_turn([], message)
    prepare_agent(agent_mock, turn)

    with client_factory() as first_client:
        created = create_conversation(first_client)

        response = send_message(
            first_client, created["id"], 0, message
        )
        assert response.status_code == 200, response.text
        saved = response.json()

    # A separate FastAPI application reads the same database state.
    with client_factory() as second_client:
        response = second_client.get(
            f"{BASE_URL}/{created['id']}"
        )
        assert response.status_code == 200
        assert response.json() == saved

    row = read_row(db_connection, created["id"])
    assert row["revision"] == 1
    assert row["history"] == turn.history
    assert row["latest_result"] == turn.latest_result

    agent_mock.assert_awaited_once_with(
        donor_name="Synthetic Test Kitchen",
        history=[],
        message=message,
    )


def test_follow_up_receives_history_and_saves_corrected_preview(
    client,
    db_connection,
    agent_mock,
):
    created = create_conversation(client)

    first_message = "We have 40 meals."
    first_turn = make_turn([], first_message, complete=True)
    prepare_agent(agent_mock, first_turn)

    first_response = send_message(
        client, created["id"], 0, first_message
    )
    assert first_response.status_code == 200

    correction = "Correction: there are 35 meals."
    second_turn = make_turn(
        first_turn.history,
        correction,
        quantity="35",
        complete=True,
    )
    prepare_agent(agent_mock, second_turn)

    response = send_message(
        client, created["id"], 1, correction
    )
    assert response.status_code == 200, response.text

    body = response.json()
    donation = body["latest_result"]["donation"]

    assert body["revision"] == 2
    assert donation["quantity"] == "35"
    assert donation["description"] == "Donation of 35 meals."
    assert donation["allergens"] == ["milk", "wheat"]
    assert donation["storage_condition"] == "chilled"

    agent_mock.assert_awaited_with(
        donor_name="Synthetic Test Kitchen",
        history=first_turn.history,
        message=correction,
    )

    row = read_row(db_connection, created["id"])
    assert row["history"] == second_turn.history


def test_stale_revision_is_rejected_before_agent_call(
    client,
    db_connection,
    agent_mock,
):
    created = create_conversation(client)
    message = "We have 40 meals."
    prepare_agent(agent_mock, make_turn([], message))

    assert send_message(
        client, created["id"], 0, message
    ).status_code == 200

    before = read_row(db_connection, created["id"])
    agent_mock.reset_mock()

    response = send_message(
        client, created["id"], 0, "Change the quantity to 35."
    )

    assert response.status_code == 409
    assert response.json()["detail"]["current_revision"] == 1
    assert read_row(db_connection, created["id"]) == before
    agent_mock.assert_not_awaited()


@pytest.mark.parametrize("status_code", [502, 503, 504])
def test_agent_failure_preserves_existing_conversation(
    client,
    db_connection,
    agent_mock,
    status_code,
):
    created = create_conversation(client)
    message = "We have 40 meals."
    prepare_agent(agent_mock, make_turn([], message))

    assert send_message(
        client, created["id"], 0, message
    ).status_code == 200

    before = read_row(db_connection, created["id"])

    async def fail_turn(**kwargs):
        # Even mutation of the supplied history must not alter storage.
        kwargs["history"].append(
            {"role": "user", "content": "This must not be saved."}
        )
        raise IntakeAgentError(status_code, "Simulated agent failure.")

    agent_mock.side_effect = fail_turn

    response = send_message(
        client, created["id"], 1, "Change the quantity to 35."
    )

    assert response.status_code == status_code
    assert response.json()["detail"] == "Simulated agent failure."
    assert read_row(db_connection, created["id"]) == before


def test_revision_change_during_agent_run_prevents_overwrite(
    client,
    db_connection,
    agent_mock,
):
    created = create_conversation(client)

    winning_turn = make_turn(
        [], "Another request updated the quantity.", quantity="20"
    )
    pending_turn = make_turn(
        [], "Set quantity to 35.", quantity="35"
    )

    async def simulate_interleaved_update(**kwargs):
        # Simulate another request committing after our initial read.
        db_connection.execute(
            update(IntakeConversationRecord)
            .where(
                IntakeConversationRecord.id == UUID(created["id"])
            )
            .values(
                revision=1,
                history=winning_turn.history,
                latest_result=winning_turn.latest_result,
            )
        )
        return pending_turn

    agent_mock.side_effect = simulate_interleaved_update

    response = send_message(
        client, created["id"], 0, "Set quantity to 35."
    )

    assert response.status_code == 409

    row = read_row(db_connection, created["id"])
    assert row["revision"] == 1
    assert row["history"] == winning_turn.history
    assert row["latest_result"] == winning_turn.latest_result


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_conversation_returns_404(
    client,
    agent_mock,
    method,
):
    conversation_id = uuid4()

    if method == "GET":
        response = client.get(f"{BASE_URL}/{conversation_id}")
    else:
        response = send_message(
            client, conversation_id, 0, "We have 40 meals."
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Intake conversation not found."
    agent_mock.assert_not_awaited()


@pytest.mark.parametrize("revision", [-1, "0", True])
def test_invalid_revision_is_rejected(
    client,
    db_connection,
    agent_mock,
    revision,
):
    created = create_conversation(client)
    before = read_row(db_connection, created["id"])

    response = send_message(
        client, created["id"], revision, "We have 40 meals."
    )

    assert response.status_code == 422
    assert read_row(db_connection, created["id"]) == before
    agent_mock.assert_not_awaited()


def test_blank_message_is_rejected(
    client,
    db_connection,
    agent_mock,
):
    created = create_conversation(client)
    before = read_row(db_connection, created["id"])

    response = send_message(client, created["id"], 0, "   ")

    assert response.status_code == 422
    assert read_row(db_connection, created["id"]) == before
    agent_mock.assert_not_awaited()


def test_complete_preview_creates_no_donation_or_confirmation(
    client,
    db_connection,
    agent_mock,
):
    def count_rows(model):
        return db_connection.execute(
            select(func.count()).select_from(model)
        ).scalar_one()

    before_drafts = count_rows(DonationDraftRecord)
    before_confirmations = count_rows(DonationConfirmationRecord)

    created = create_conversation(client)
    message = "Complete synthetic donation details."
    prepare_agent(
        agent_mock,
        make_turn([], message, quantity="35", complete=True),
    )

    response = send_message(
        client, created["id"], 0, message
    )

    assert response.status_code == 200
    result = response.json()["latest_result"]

    assert result["status"] == "needs_review"
    assert result["missing_fields"] == []
    assert result["saved"] is False
    assert result["donor_confirmation_required"] is True

    assert count_rows(DonationDraftRecord) == before_drafts
    assert count_rows(DonationConfirmationRecord) == before_confirmations


def test_conversations_keep_separate_histories(
    client,
    db_connection,
    agent_mock,
):
    first = create_conversation(client)
    second = create_conversation(client, "Other Test Kitchen")

    first_message = "We have 40 meals."
    first_turn = make_turn([], first_message)
    prepare_agent(agent_mock, first_turn)

    assert send_message(
        client, first["id"], 0, first_message
    ).status_code == 200

    first_before = read_row(db_connection, first["id"])

    second_message = "We have 10 meals."
    second_turn = make_turn(
        [],
        second_message,
        quantity="10",
        donor_name="Other Test Kitchen",
    )
    prepare_agent(agent_mock, second_turn)

    response = send_message(
        client, second["id"], 0, second_message
    )

    assert response.status_code == 200
    agent_mock.assert_awaited_with(
        donor_name="Other Test Kitchen",
        history=[],
        message=second_message,
    )

    assert read_row(db_connection, first["id"]) == first_before
    assert read_row(
        db_connection, second["id"]
    )["history"] == second_turn.history
    
    
# Tests for saving a reviewed conversation preview.


def count_records(connection, model):
    return connection.execute(
        select(func.count()).select_from(model)
    ).scalar_one()


def ready_conversation(client, agent_mock):
    created = create_conversation(client)
    message = "Complete synthetic donation details."
    turn = make_turn([], message, quantity="35", complete=True)
    prepare_agent(agent_mock, turn)

    response = send_message(
        client, created["id"], 0, message
    )
    assert response.status_code == 200, response.text

    conversation = response.json()
    assert conversation["latest_result"]["status"] == "needs_review"
    return conversation


def save_reviewed_preview(client, conversation, **overrides):
    body = {
        "expected_revision": conversation["revision"],
        "acknowledged": True,
    }
    body.update(overrides)

    return client.post(
        f"{BASE_URL}/{conversation['id']}/save-draft",
        json=body,
    )


def test_save_creates_linked_unconfirmed_draft(
    client,
    db_connection,
    agent_mock,
):
    conversation = ready_conversation(client, agent_mock)
    before_drafts = count_records(db_connection, DonationDraftRecord)
    before_confirmations = count_records(
        db_connection, DonationConfirmationRecord
    )
    agent_mock.reset_mock()

    response = save_reviewed_preview(client, conversation)

    assert response.status_code == 200, response.text
    body = response.json()
    draft = body["donation"]

    assert body["created"] is True
    assert body["conversation_revision"] == conversation["revision"]
    assert draft["status"] == "ready_for_confirmation"
    assert draft["revision"] == 1
    assert draft["donor_confirmation_required"] is True
    assert draft["confirmation"] is None
    assert draft["donation"]["description"] == "Donation of 35 meals."

    row = read_row(db_connection, conversation["id"])
    assert str(row["donation_id"]) == draft["id"]

    assert count_records(
        db_connection, DonationDraftRecord
    ) == before_drafts + 1
    assert count_records(
        db_connection, DonationConfirmationRecord
    ) == before_confirmations

    agent_mock.assert_not_awaited()


def test_repeated_save_returns_same_draft(
    client,
    db_connection,
    agent_mock,
):
    conversation = ready_conversation(client, agent_mock)
    before = count_records(db_connection, DonationDraftRecord)

    first = save_reviewed_preview(client, conversation)
    second = save_reviewed_preview(client, conversation)

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["donation"]["id"] == second.json()["donation"]["id"]

    assert count_records(
        db_connection, DonationDraftRecord
    ) == before + 1


def test_incomplete_preview_cannot_be_saved(
    client,
    db_connection,
    agent_mock,
):
    conversation = create_conversation(client)
    message = "We have 40 meals."
    prepare_agent(agent_mock, make_turn([], message))

    response = send_message(
        client, conversation["id"], 0, message
    )
    assert response.status_code == 200
    conversation = response.json()

    before = count_records(db_connection, DonationDraftRecord)
    response = save_reviewed_preview(client, conversation)

    assert response.status_code == 409
    assert count_records(db_connection, DonationDraftRecord) == before
    assert read_row(
        db_connection, conversation["id"]
    )["donation_id"] is None


def test_save_rejects_wrong_revision(
    client,
    db_connection,
    agent_mock,
):
    conversation = ready_conversation(client, agent_mock)
    before = read_row(db_connection, conversation["id"])
    before_drafts = count_records(db_connection, DonationDraftRecord)

    response = save_reviewed_preview(
        client,
        conversation,
        expected_revision=conversation["revision"] + 1,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["current_revision"] == (
        conversation["revision"]
    )
    assert read_row(db_connection, conversation["id"]) == before
    assert count_records(
        db_connection, DonationDraftRecord
    ) == before_drafts


@pytest.mark.parametrize("acknowledged", [False, "true", 1])
def test_save_requires_explicit_boolean_acknowledgement(
    client,
    db_connection,
    agent_mock,
    acknowledged,
):
    conversation = ready_conversation(client, agent_mock)
    before = count_records(db_connection, DonationDraftRecord)

    response = save_reviewed_preview(
        client,
        conversation,
        acknowledged=acknowledged,
    )

    assert response.status_code == 422
    assert count_records(db_connection, DonationDraftRecord) == before
    assert read_row(
        db_connection, conversation["id"]
    )["donation_id"] is None


def test_save_requires_acknowledgement_field(
    client,
    db_connection,
    agent_mock,
):
    conversation = ready_conversation(client, agent_mock)
    before = count_records(db_connection, DonationDraftRecord)

    response = client.post(
        f"{BASE_URL}/{conversation['id']}/save-draft",
        json={"expected_revision": conversation["revision"]},
    )

    assert response.status_code == 422
    assert count_records(db_connection, DonationDraftRecord) == before


def test_save_unknown_conversation_returns_404(client, agent_mock):
    response = client.post(
        f"{BASE_URL}/{uuid4()}/save-draft",
        json={
            "expected_revision": 1,
            "acknowledged": True,
        },
    )

    assert response.status_code == 404
    agent_mock.assert_not_awaited()


def test_saved_conversation_rejects_messages_before_agent_call(
    client,
    db_connection,
    agent_mock,
):
    conversation = ready_conversation(client, agent_mock)
    saved = save_reviewed_preview(client, conversation)
    assert saved.status_code == 200

    before = read_row(db_connection, conversation["id"])
    agent_mock.reset_mock()

    response = send_message(
        client,
        conversation["id"],
        conversation["revision"],
        "Change the quantity to 20 meals.",
    )

    assert response.status_code == 409
    assert response.json()["detail"]["donation_id"] == (
        saved.json()["donation"]["id"]
    )
    assert read_row(db_connection, conversation["id"]) == before
    agent_mock.assert_not_awaited()


def test_save_during_agent_run_blocks_late_agent_result(
    client,
    db_connection,
    agent_mock,
):
    conversation = ready_conversation(client, agent_mock)
    before = read_row(db_connection, conversation["id"])
    linked_draft_id = uuid4()

    pending_turn = make_turn(
        before["history"],
        "Change the quantity to 20 meals.",
        quantity="20",
        complete=True,
    )

    async def simulate_save_during_run(**kwargs):
        # Simulate a save occurring after the message endpoint reads
        # its snapshot but before it attempts to write the agent result.
        donation = DonationDraft.model_validate(
            before["latest_result"]["donation"]
        )

        db_connection.execute(
            DonationDraftRecord.__table__.insert().values(
                id=linked_draft_id,
                **donation.model_dump(),
            )
        )

        db_connection.execute(
            update(IntakeConversationRecord)
            .where(
                IntakeConversationRecord.id == UUID(conversation["id"])
            )
            .values(donation_id=linked_draft_id)
        )

        return pending_turn

    agent_mock.side_effect = simulate_save_during_run

    response = send_message(
        client,
        conversation["id"],
        conversation["revision"],
        "Change the quantity to 20 meals.",
    )

    assert response.status_code == 409

    after = read_row(db_connection, conversation["id"])
    assert after["donation_id"] == linked_draft_id
    assert after["revision"] == before["revision"]
    assert after["history"] == before["history"]
    assert after["latest_result"] == before["latest_result"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", "0"),
        ("allergens", None),
    ],
)
def test_save_revalidates_preview_instead_of_trusting_status(
    client,
    db_connection,
    agent_mock,
    field,
    value,
):
    conversation = ready_conversation(client, agent_mock)
    preview = deepcopy(conversation["latest_result"])

    # Deliberately make the stored fields contradict "needs_review".
    preview["donation"][field] = value

    db_connection.execute(
        update(IntakeConversationRecord)
        .where(
            IntakeConversationRecord.id == UUID(conversation["id"])
        )
        .values(latest_result=preview)
    )

    before = count_records(db_connection, DonationDraftRecord)
    response = save_reviewed_preview(client, conversation)

    assert response.status_code == 409
    assert count_records(db_connection, DonationDraftRecord) == before
    assert read_row(
        db_connection, conversation["id"]
    )["donation_id"] is None


def test_failed_save_rolls_back_draft_and_conversation_link(
    client,
    db_connection,
    agent_mock,
    monkeypatch,
):
    conversation = ready_conversation(client, agent_mock)
    before = read_row(db_connection, conversation["id"])
    before_drafts = count_records(db_connection, DonationDraftRecord)

    def fail_after_insert(*args, **kwargs):
        raise HTTPException(
            status_code=500,
            detail="Simulated failure after creating the draft.",
        )

    monkeypatch.setattr(
        conversation_api,
        "build_saved_response",
        fail_after_insert,
    )

    response = save_reviewed_preview(client, conversation)

    assert response.status_code == 500
    assert count_records(
        db_connection, DonationDraftRecord
    ) == before_drafts
    assert read_row(db_connection, conversation["id"]) == before