\# RELAY



\## A Multimodal Agentic AI System for Surplus Food Rescue Coordination



RELAY is an AML3403 capstone research prototype for human-supervised

surplus-food rescue coordination.



The implemented backend supports validating, saving, retrieving,

updating, and explicitly confirming donation drafts.



AI intake, agent coordination, recipient matching, transport optimization,

and the frontend are planned components and are not implemented in the

current verified workflow.



\## Current Features



\- Structured donation validation.

\- PostgreSQL storage for donation drafts.

\- Retrieval of saved drafts after restarting the backend.

\- Draft updates with revision tracking.

\- Explicit confirmation of the reviewed revision.

\- Rejection of incomplete, outdated, or expired confirmation requests.

\- Storage of the approved donation details as a confirmation snapshot.

\- Repeated confirmation returns the existing record.

\- Confirmed donations cannot be edited through the draft-update endpoint.

\- Separate development and test databases.

\- Alembic database migrations.

\- Automated validation, persistence, and confirmation tests.



\## Current Limitations



This is a local research prototype using synthetic examples.



\- Donor authentication and ownership checks are not implemented.

\- The supplied confirmer name is recorded but is not verified identity.

\- Confirmation records a declaration; it does not independently establish

&#x20; food safety or allergen absence.

\- A confirmed record is historical evidence of confirmation, not proof

&#x20; that its pickup window is still open.

\- AI extraction, matching, routing, and a user interface remain planned.

\- Run the current API locally; it is not ready for public deployment.



\## Technology Stack



\- Python 3.12

\- Anaconda

\- Spyder

\- FastAPI and Uvicorn

\- Pydantic and Pydantic Settings

\- PostgreSQL 18

\- SQLAlchemy and Psycopg

\- Alembic

\- pytest and FastAPI TestClient



\## Project Structure



```text

RELAY\_project/

├── backend/

│   ├── \_\_init\_\_.py

│   ├── app/

│   │   ├── \_\_init\_\_.py

│   │   ├── main.py

│   │   ├── config.py

│   │   ├── database.py

│   │   ├── models.py

│   │   ├── api/

│   │   │   ├── \_\_init\_\_.py

│   │   │   ├── health.py

│   │   │   └── donations.py

│   │   └── schemas/

│   │       ├── \_\_init\_\_.py

│   │       ├── donation.py

│   │       ├── donation\_record.py

│   │       └── confirmation.py

│   └── migrations/

│       ├── env.py

│       ├── README

│       ├── script.py.mako

│       └── versions/

├── tests/

│   ├── test\_donations\_api.py

│   └── test\_donation\_persistence.py

├── frontend/

│   └── README.md

├── data/

│   └── README.md

├── experiments/

│   └── README.md

├── docs/

│   └── README.md

├── alembic.ini

├── environment.yml

├── .env.example

├── .gitignore

└── README.md

```



The local `.env` file is deliberately excluded from Git.

The frontend, data, experiments, and docs directories contain initial

documentation placeholders, not completed implementations.



\## Setup on Windows



These instructions are for a fresh local checkout.



\### 1. Prerequisites



Install:



\- Anaconda or Miniconda.

\- PostgreSQL 18, including Command Line Tools.

\- Git, if cloning the repository.



Download or clone the repository and open Anaconda PowerShell in its

root folder, where `alembic.ini` and `environment.yml` are located.



\### 2. Create the Python Environment



```powershell

conda env create -f environment.yml

conda activate relay

```



The exported environment reflects the Windows development environment.

Recreation on a clean machine has not yet been independently verified.



\### 3. Create the PostgreSQL Account and Databases



Ensure the PostgreSQL service is running.



Connect as the PostgreSQL administrator:



```powershell

\& 'C:\\Program Files\\PostgreSQL\\18\\bin\\psql.exe' -h localhost -p 5432 -U postgres -d postgres

```



Enter the administrator password when prompted.



At the PostgreSQL prompt, run:



```sql

CREATE ROLE relay\_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;

```



Set its password using this command:



```text

\\password relay\_app

```



Enter a new local password twice. After the password prompts finish,

run the following database-creation statements:



```sql

CREATE DATABASE relay OWNER relay\_app;

CREATE DATABASE relay\_test OWNER relay\_app;

```



Exit PostgreSQL:



```text

\\q

```



These creation commands are for initial setup. Existing accounts and

databases do not need to be recreated.



\### 4. Configure Local Credentials



On a fresh checkout, copy the configuration example:



```powershell

Copy-Item -LiteralPath '.env.example' -Destination '.env'

notepad .env

```



Replace the password placeholder with the password created for

`relay\_app`. Keep `.env` private.



If the password contains a single quote, escape that character as `\\'`

inside the single-quoted value.



Keep the development database name as:



```text

RELAY\_DB\_NAME=relay

```



\### 5. Apply Development Database Migrations



```powershell

python -m alembic upgrade head

```



\### 6. Apply Test Database Migrations



Run this entire block:



```powershell

$relayPreviousDbName = $env:RELAY\_DB\_NAME



try {

&#x20;   $env:RELAY\_DB\_NAME = 'relay\_test'



&#x20;   python -m alembic upgrade head



&#x20;   if ($LASTEXITCODE -ne 0) {

&#x20;       throw 'Test database migration failed.'

&#x20;   }

}

finally {

&#x20;   if ($null -eq $relayPreviousDbName) {

&#x20;       Remove-Item Env:RELAY\_DB\_NAME -ErrorAction SilentlyContinue

&#x20;   }

&#x20;   else {

&#x20;       $env:RELAY\_DB\_NAME = $relayPreviousDbName

&#x20;   }

}

```



The block restores the previous database environment setting afterwards.



\### 7. Start the Backend



```powershell

python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

```



Keep this terminal running.



Open:



\- API documentation: http://127.0.0.1:8000/docs

\- Health check: http://127.0.0.1:8000/api/v1/health



The root URL has no endpoint and may return 404.



\## API Endpoints



| Method | Path | Purpose |

|---|---|---|

| GET | `/api/v1/health` | Check API availability |

| POST | `/api/v1/donations/validate` | Validate without saving |

| POST | `/api/v1/donations` | Save a draft |

| GET | `/api/v1/donations/{donation\_id}` | Retrieve a saved draft |

| PUT | `/api/v1/donations/{donation\_id}` | Replace an unconfirmed draft |

| POST | `/api/v1/donations/{donation\_id}/confirm` | Confirm the reviewed revision |



PUT requires the full draft body. Omitted optional fields return to

their defaults rather than retaining their previous values.



Each successful PUT advances the draft revision.



\## Donation and Confirmation Rules



\- Quantities must be positive.

\- Meals, boxes, and packages require whole-number quantities.

\- Timestamps must include a timezone.

\- Pickup deadlines must follow pickup availability.

\- Unknown allergens are represented as `null`.

\- An explicit declaration of no allergens is represented as `\[]`.

\- Prepared meals require preparation time before confirmation.

\- Saving a draft does not confirm it.

\- `ready\_for\_confirmation` indicates completeness; the confirmation

&#x20; endpoint also checks whether the pickup deadline has passed.



To confirm a saved draft, use its actual ID in the URL and its current

revision in the request:



```json

{

&#x20; "expected\_revision": 1,

&#x20; "confirmed\_by": "Demo Donor",

&#x20; "acknowledged": true

}

```



Use the returned revision instead of assuming it is always 1.



For demonstrations, use synthetic information and a future pickup

deadline. Swagger's generated example values are placeholders and may

not form a valid request.



\## Running Tests



PostgreSQL must be running, and both databases must be migrated.

Uvicorn does not need to be running.



From the repository root:



```powershell

python -m pytest tests/test\_donations\_api.py tests/test\_donation\_persistence.py -v

```



Latest recorded development result:



```text

32 passed, 2 warnings in 2.25s

```



Runtime may vary.



Coverage:



\- 9 donation-validation cases.

\- 11 persistence and database API cases.

\- 12 confirmation cases.



Database tests target `relay\_test` and roll back test changes.



The latest run reported Starlette/httpx and AnyIO deprecation warnings.

Dependency compatibility review remains pending.



\## Planned Development



\- AI-assisted text and image intake.

\- Authenticated donor access and ownership checks.

\- Synthetic recipient and driver datasets.

\- Deterministic recipient matching.

\- Transport allocation and routing.

\- Human-reviewed agent coordination.

\- React and TypeScript frontend.

\- Held-out evaluation and baseline comparisons.



\## Configuration and Data Handling



Do not commit:



\- `.env` or real credentials.

\- Installed Python environments.

\- Database backups containing private information.

\- Generated caches.

\- Frontend `node\_modules`.



Use `.env.example` for configuration placeholders and synthetic data

for demonstrations.

