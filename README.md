# NexaAssist

Agentic Customer Support & Workflow Automation Platform.

NexaAssist takes an inbound customer request, decides what it is, answers it
from a knowledge base it can cite, or drives it through a defined workflow —
and records enough about what it did that you can tell afterwards why.

It runs without any of its infrastructure. With no database, no Redis and no
provider key it still starts, serves, and reports precisely which components
are unconfigured; every external dependency has a deterministic in-process
counterpart, which is what lets the test suite run offline and a new checkout
run at all. See [`docs/overview.md`](docs/overview.md) for what it does and
[`docs/architecture.md`](docs/architecture.md) for how.

## API

Routes are versioned: version 1 is served under `/api/v1`. The pre-v1
`/api/health` alias has been removed — see
[`docs/architecture.md`](docs/architecture.md#api-versioning).

| Endpoint | Purpose |
| --- | --- |
| `GET /health` · `GET /ready` | Liveness, and readiness with a per-component breakdown. |
| `POST /assistant/messages` | The main entry point: a customer message in, a grounded answer out. |
| `POST /intent/analyze` | Classify a message without answering it. |
| `POST /documents` · `GET /documents` | Ingest and list knowledge-base documents. |
| `POST /documents/answer` | Answer strictly from retrieved documents, with citations. |
| `POST /conversations` · `GET /conversations/{conversation_id}` | Multi-turn sessions and their history. |
| `POST /tickets` · `GET /tickets` | The support-ticket domain. |
| `POST /ws/ticket` → `WS /ws` | Exchange an API key for a short-lived ticket, then stream over a socket. |

Interactive docs are at `/docs`; the schema is at `/openapi.json`.

## Repository layout

| Path | Contents |
| --- | --- |
| `docs/` | Design documents: overview, architecture, tech stack, prompt design, milestones. |
| `backend/` | Python + FastAPI service. |
| `frontend/` | React + TypeScript client. |
| `tests/` | Test suite, split by target (`backend/`, `frontend/`). |

## Running in a container

The backend ships a two-stage image. It is built from the `backend/` directory,
so the repository root — and the git-ignored `.env` beside it — is not in the
build context at all.

```bash
docker build -f backend/Dockerfile -t nexaassist-backend backend
```

Configuration is passed at run time, never baked in:

```bash
docker run --rm -p 8000:8000 --env-file .env nexaassist-backend
```

The image runs as a non-root user, installs from `backend/requirements.lock`
so two builds install the same versions, and **does not migrate the database on
startup**. Applying a migration stays an explicit action against a database you
chose:

```bash
cd backend && alembic upgrade head
```

### The whole stack

`compose.yaml` brings up the backend, the client, PostgreSQL (with pgvector)
and Redis together. The database password is a required variable with no
default — the stack refuses to start rather than run on a password that is also
in this repository:

```bash
NEXA_DB_PASSWORD=choose-anything-local docker compose up --build
```

The client is then on <http://127.0.0.1:15173> and the API on
<http://127.0.0.1:18000>. PostgreSQL and Redis publish no ports: they exist for
the backend, which reaches them over the compose network. That is deliberate —
a published 5432 would sit next to whatever PostgreSQL you already run, and
getting that wrong means writing to the wrong database. To get a shell on one:

```bash
docker compose exec db psql -U nexa -d nexaassist
```

Migrations never run on their own. The `migrate` service sits behind a profile,
so `up` cannot start it and nothing waits on it:

```bash
NEXA_DB_PASSWORD=... docker compose --profile migrate run --rm migrate
```

If a `.env` exists it is passed to the backend at run time — so the stack uses
whichever `LLM_PROVIDER` it names, and `groq` means real, billable calls. Set
`LLM_PROVIDER=static` for a stack that answers deterministically and calls
nothing.

Two notes on secrets. `docker compose config` resolves `.env` and prints its
values, including the provider key — do not paste its output anywhere. And the
client's API URL is inlined by Vite at build time, so it is a build argument
(`VITE_API_BASE_URL`), which means it is visible in the image's history: it is a
URL, and nothing secret belongs there.

### What keeps the images honest

Both build contexts carry a `.dockerignore` that excludes `.env` and every
variant of it. The Dockerfiles copy by name, so nothing depends on that today —
it is the second defence for the day someone writes `COPY . .`, and two things
must then go wrong before a key reaches an image.

`tests/backend/test_container_build.py` and `test_compose_stack.py` read the
Dockerfiles and `compose.yaml` as text, so they run anywhere. The smoke tests in
`tests/backend/docker/` build the images and look inside them — no `.env` in the
filesystem, no credential in the layer history or the served bundle, no compiler
or test runner in the runtime image, and a container that starts and answers
with nothing configured at all. They skip themselves when no Docker daemon is
reachable, so the suite still passes without one.

## Prerequisites

Only the first is required. Everything below it is optional, and the service
tells you at `/ready` which of them it is running without.

- **Python 3.11+** — the floor the project is tested against; the image runs 3.12.
- **Node.js 20+** — for the client.
- **PostgreSQL 15 with pgvector** — persistence and retrieval. Without it the
  API serves, and anything that needs to store or retrieve returns a clear
  `database_not_configured` rather than failing obscurely.
- **Redis 7** — the durable job queue, the shared rate limiter, and realtime
  tickets. Each falls back to an in-process implementation, correct for one
  worker and wrong for several.
- **A Groq API key** — for real model calls. `LLM_PROVIDER=static` answers
  deterministically and calls nothing, which is what the tests use.
- **Docker** — only for the container workflow above.

## Getting started

### 1. Configure environment

```bash
cp .env.example .env
```

The defaults run the service locally with no infrastructure at all. `.env` is
git-ignored and is the only place a real credential belongs — `.env.example`
names every variable and carries none of the values, and a test fails if a new
setting is added without being documented there.

### 2. Backend

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements-dev.txt
```

Run the API from the repository root:

```bash
uvicorn app.main:app --reload --app-dir backend
```

`/ready` reports what it found:

```bash
curl http://127.0.0.1:8000/api/v1/ready
```

### 3. Database (optional)

Migrations are never applied automatically — not at startup, not by the
container, not by `docker compose up`. Applying one is a decision about a
database you chose:

```bash
createdb nexaassist && cd backend && alembic upgrade head
```

Set `DATABASE_URL` in `.env` to point at it. `alembic` owns the schema
outright; nothing in the application creates a table.

### 4. Tests

```bash
pytest
```

Everything that needs infrastructure skips itself when that infrastructure is
absent, so this passes on a fresh clone with nothing installed. To run those
too, make PostgreSQL and Redis reachable and create the one database the suite
is allowed to touch:

```bash
createdb nexaassist_test && pytest
```

The suite blocks outbound network connections, so no test can reach a real
provider. Docker-backed tests build the images and skip when no daemon is
running.

### 5. Frontend

```bash
cd frontend && npm ci && npm run dev
```

The client is served at <http://127.0.0.1:5173> and expects the API at
`VITE_API_BASE_URL`. Run `npm run test` for its suite and `npm run typecheck`
for types.

## Conventions

- Configuration comes from environment variables only — never hardcoded, never
  committed. Every new variable must be added to `.env.example` with a comment,
  and a test enforces it.
- Documentation in `docs/` is updated in the same change as the code it
  describes.
- Each milestone in `docs/milestones.md` lands as its own focused change set.
- Every external dependency has a deterministic in-process counterpart, and
  the protocol is what the application depends on — not the implementation.
- Nothing creates or migrates a schema except Alembic, run deliberately.
