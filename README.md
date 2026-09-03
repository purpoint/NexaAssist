# NexaAssist

Agentic Customer Support & Workflow Automation Platform.

> **Status: foundation only.** This repository currently contains the project
> skeleton — a versioned API surface, configuration, logging, error handling,
> and a single health endpoint. No agents, retrieval, database, cache,
> authentication, or business workflows are implemented yet. See
> [`docs/milestones.md`](docs/milestones.md) for what comes next.

## API

Routes are versioned: version 1 is served under `/api/v1`. The pre-v1
`/api/health` alias has been removed — see
[`docs/architecture.md`](docs/architecture.md#api-versioning).

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

A local orchestration for PostgreSQL, Redis and the frontend arrives with the
rest of M22.

## Prerequisites

- Python 3.11+
- Node.js 20+ (only needed once frontend work begins)

## Getting started

### 1. Configure environment

```bash
cp .env.example .env
```

The defaults in `.env.example` are sufficient to run locally; nothing in it is
secret today.

### 2. Backend

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt
```

Run the API from the repository root:

```bash
uvicorn app.main:app --reload --app-dir backend
```

Then check the health endpoint:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Interactive API docs are served at `http://127.0.0.1:8000/docs`.

### 3. Tests

```bash
pip install -r backend/requirements-dev.txt && pytest
```

### 4. Frontend

Dependencies are declared in `frontend/package.json` but intentionally **not
installed** yet. When frontend work starts:

```bash
cd frontend && npm install && npm run dev
```

## Conventions

- Configuration comes from environment variables only — never hardcoded, never
  committed. Every new variable must be added to `.env.example` with a comment.
- Documentation in `docs/` is updated in the same change as the code it
  describes.
- Each milestone in `docs/milestones.md` lands as its own focused change set.
