# RELAY

## A Multimodal Agentic AI System for Surplus Food Rescue Coordination

RELAY is an AML3403 capstone research prototype for human-supervised surplus-food rescue coordination.

The current implementation supports AI text intake, a conversational intake agent, persistent conversation history, reviewed-preview saving, and explicit donation confirmation.

Image intake, RAG, recipient matching, transport planning, multi-agent coordination, and the frontend remain planned.

## Implemented Features

- Structured donation validation using Pydantic.
- PostgreSQL storage for donation drafts and confirmation records.
- Draft retrieval and revision-based updates.
- Explicit confirmation of a complete, reviewed donation revision.
- Rejection of incomplete, outdated, or expired confirmation requests.
- AI text extraction using the OpenAI Responses API.
- A conversational intake agent using the OpenAI Agents SDK.
- A validation tool that checks proposed donation fields.
- Conversation history and latest previews stored in PostgreSQL.
- Follow-up messages and corrections using saved conversation history.
- Revision checks that reject stale conversation requests.
- Direct saving of a reviewed conversation preview as an unconfirmed draft.
- Repeated save requests returning the same linked draft.
- Blocking further agent messages after a conversation is saved.
- Transaction rollback and separate development/test databases.

## Verified Milestone

Latest recorded local test result, November 6, 2026:

```text
75 passed, 1 warning in 5.54s
```

Live demonstrations have covered:

- Incomplete and complete AI extraction.
- Conversation continuation and quantity corrections.
- Consistent quantity and generated preview description.
- Retrieving and continuing a conversation after a server restart.
- Rejection of stale conversation revisions.
- Saving a reviewed preview without manually copying its donation fields.
- Repeated saves returning the existing draft.
- Rejection of changes through a saved conversation.

The automated AI-related tests use simulated agent or model responses. They verify application behavior, not live model accuracy. Held-out model evaluation remains planned.

## Technology Stack

- Python 3.12, Anaconda, and Spyder
- FastAPI and Uvicorn
- Pydantic and Pydantic Settings
- PostgreSQL 18
- SQLAlchemy, Psycopg, and Alembic
- OpenAI Python SDK and OpenAI Agents SDK
- `gpt-6-luna` for the demonstrated intake workflows
- pytest and FastAPI TestClient

RELAY currently uses a pretrained model. No model training or fine-tuning has been completed.

## Project Structure

```text
RELAY_project/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── intake_agent.py
│   │   ├── api/
│   │   │   ├── health.py
│   │   │   ├── donations.py
│   │   │   ├── intake.py
│   │   │   └── intake_conversations.py
│   │   └── schemas/
│   │       ├── donation.py
│   │       ├── donation_record.py
│   │       ├── confirmation.py
│   │       ├── intake.py
│   │       └── intake_conversation.py
│   └── migrations/
│       ├── env.py
│       └── versions/
├── experiments/
│   ├── README.md
│   ├── intake_smoke_test.py
│   └── intake_agent_demo.py
├── tests/
│   ├── test_donations_api.py
│   ├── test_donation_persistence.py
│   ├── test_intake_api.py
│   └── test_intake_conversations.py
├── frontend/
├── data/
├── docs/
├── alembic.ini
├── environment.yml
├── .env.example
├── .gitignore
└── README.md
```

Package initialization files and generated caches are omitted from this diagram. The frontend and data directories are placeholders.

## Windows Setup

These instructions are for a fresh local checkout.

### 1. Prerequisites and Environment

Install Anaconda or Miniconda, PostgreSQL 18 with command-line tools, and Git.

Clone the repository:

```powershell
git clone https://github.com/gouthamrp576-stack/RELAY.git
Set-Location RELAY
conda env create -f environment.yml
conda activate relay
```

The environment file reflects the Windows development environment. Recreation on a clean machine has not yet been independently verified.

### 2. Create the Database Account

Ensure PostgreSQL is running. Connect as its administrator:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -h localhost -p 5432 -U postgres -d postgres
```

Create the application role:

```sql
CREATE ROLE relay_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
```

Set its password interactively:

```text
\password relay_app
```

After completing both password prompts, create the databases:

```sql
CREATE DATABASE relay OWNER relay_app;
CREATE DATABASE relay_test OWNER relay_app;
```

Exit:

```text
\q
```

Existing accounts and databases do not need to be recreated.

### 3. Configure Credentials

Create `.env` only if it does not already exist:

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
notepad .env
```

Replace the database password placeholder with your local password.

For live AI requests, replace the OpenAI API key placeholder with your own key. Live model requests use your API account's usage allowance and billing.

Keep the development database name as `relay`.

Never commit `.env` or a separate file containing an API key.

### 4. Apply Database Migrations

Development database:

```powershell
$env:RELAY_DB_NAME = 'relay'
python -m alembic upgrade head
```

Test database — run the entire block:

```powershell
$relayPreviousDbName = $env:RELAY_DB_NAME

try {
    $env:RELAY_DB_NAME = 'relay_test'
    python -m alembic upgrade head

    if ($LASTEXITCODE -ne 0) {
        throw 'Test database migration failed.'
    }
}
finally {
    if ($null -eq $relayPreviousDbName) {
        Remove-Item Env:RELAY_DB_NAME -ErrorAction SilentlyContinue
    }
    else {
        $env:RELAY_DB_NAME = $relayPreviousDbName
    }
}
```

The latest migration in this milestone is `c37ed5dc4b1d`.

### 5. Start the Backend

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

- [API documentation](http://127.0.0.1:8000/docs)
- [Health check](http://127.0.0.1:8000/api/v1/health)

Keep the server terminal open. Use another terminal for tests and Git commands.

## API Endpoints

All paths below use the `/api/v1` prefix.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Check API availability |
| POST | `/donations/validate` | Validate donation fields |
| POST | `/donations` | Save an unconfirmed draft |
| GET | `/donations/{donation_id}` | Retrieve a donation |
| PUT | `/donations/{donation_id}` | Replace an unconfirmed draft |
| POST | `/donations/{donation_id}/confirm` | Confirm the reviewed revision |
| POST | `/intake/text` | Extract a preview from one message |
| POST | `/intake/conversations` | Create a persistent conversation |
| GET | `/intake/conversations/{conversation_id}` | Retrieve conversation state |
| POST | `/intake/conversations/{conversation_id}/messages` | Continue the conversation |
| POST | `/intake/conversations/{conversation_id}/save-draft` | Save the reviewed preview |

## Conversational Intake Workflow

1. Create a conversation with the donor name.
2. Send a message with the returned conversation revision as `expected_revision`.
3. Read `latest_result.agent_reply`, the proposed donation, and missing fields.
4. Supply follow-up information using the latest returned revision.
5. Review the complete preview.
6. Call `save-draft` with that revision and `"acknowledged": true`.
7. Review the returned donation and separately confirm its current donation revision.

Conversation revisions and donation revisions are separate counters.

The first save returns `created: true`. Repeating the request with the same reviewed conversation revision returns `created: false` and the existing linked draft.

After saving, further conversation messages are rejected. Use the donation workflow for subsequent draft changes.

`latest_result` preserves the last agent-turn preview. The conversation's top-level `donation_id` identifies the saved donation; retrieve that donation to see its current state.

## Validation and Confirmation Rules

- Quantities must be positive.
- Meals, boxes, and packages require whole-number quantities.
- Timestamps require timezone information.
- Pickup deadlines must follow pickup availability.
- Unknown allergens remain `null`.
- An explicitly reported empty allergen list is preserved as `[]`.
- Prepared meals require preparation time before confirmation.
- Saving a preview or draft does not confirm a donation.
- Confirmation requires explicit acknowledgement and the current donation revision.
- Expired pickup deadlines prevent initial confirmation.
- Repeated confirmation returns the existing confirmation record.
- Confirmed donations cannot be edited through the draft-update endpoint.

PUT replaces the draft fields and advances its revision. Omitted optional fields return to their defaults.

Use synthetic details and future pickup dates for live demonstrations. Swagger's generated examples are placeholders, not necessarily valid donation requests.

## Running Tests

PostgreSQL must be running and `relay_test` must be migrated. Uvicorn does not need to be running.

```powershell
python -m pytest tests -v
```

Test groups:

| File | Cases |
|---|---:|
| `test_donations_api.py` | 9 |
| `test_donation_persistence.py` | 23 |
| `test_intake_api.py` | 13 |
| `test_intake_conversations.py` | 30 |
| **Total** | **75** |

Database tests target `relay_test` and roll back their changes. Automated AI-related tests replace live model calls with simulated responses.

The recorded run reported one AnyIO `BlockingPortal` deprecation warning. It did not fail the suite. Runtime may vary.

Interleaved-update tests simulate relevant operation ordering; they are not concurrent load tests.

## Local Experiments

From the repository root:

```powershell
python experiments/intake_smoke_test.py
```

```powershell
python -m experiments.intake_agent_demo
```

Both experiments make live model requests. The terminal agent's memory lasts only until that program closes. The backend conversation API stores its history in PostgreSQL.

## Limitations and Planned Work

- Donor authentication and ownership checks are not implemented.
- Supplied donor and confirmer names are not verified identities.
- Structured output and validation do not establish factual correctness.
- Confirmation does not independently establish food safety or allergen absence.
- A historical confirmation does not mean its pickup window is still open.
- Held-out extraction and agent evaluation remain to be completed.
- RAG with source-backed policy explanations is planned.
- Image intake, recipient matching, allocation, routing, and replanning are planned.
- Multi-agent coordination and the frontend are planned.
- The current prototype is intended for local use with synthetic examples.

## Repository Hygiene

Exclude credentials, `.env`, API-key text files, installed environments, generated caches, and private database backups.

Use `.env.example` for placeholders. Use synthetic data in demonstrations and tests.