# NexaAssist

**Agentic customer support, built so every answer can be checked.**

[**Live demo**](https://nexa-assist-henna.vercel.app) · [API docs](https://nexaassist-backend-k0mn.onrender.com/docs) · [Architecture](docs/architecture.md)

NexaAssist takes an inbound customer message, works out what it is, and either
answers it from a knowledge base it can cite, hands it to a person, or declines
— and records enough about the decision that you can explain it afterwards.

The distinguishing constraint is that none of that is a black box. Every answer
carries its sources. Every escalation happened because a stated rule fired. A
support system that cannot say *why* it said something is one nobody can be
accountable for.

> The demo runs on a free tier: the first request after a quiet period takes
> about 20 seconds to wake the backend, and it is protected by an API key. To
> run it yourself, `docker compose up` gets the whole stack in about a minute.

---

## What it does

Ask *"How long does standard shipping take?"* and you get the answer **from your
document**, with the passage it came from:

```
NexaAssist
Standard shipping takes 3 to 5 business days within the country
and 7 to 14 business days internationally.

▾ Sources  1                        Grounded in your knowledge base
  📄 Shipping and delivery
  │ Standard shipping takes 3 to 5 business days within the country
  │ and 7 to 14 business days internationally. Express shipping…
  Passage 1
```

Ask *"I want a refund for my order"* and policy stops the assistant resolving it
alone:

```
NexaAssist
I have passed this to a support agent, who will confirm the details with you.

● Human support requested
  This conversation has been handed to a support agent for review.
```

Ask something the documents do not cover and it declines rather than inventing
an answer.

---

## How it works

```
message
   │
   ├─ classify ─────────  six intents, with a confidence floor below which
   │                      nothing is dispatched on a guess
   │
   ├─ route ────────────  documented questions → knowledge base (pgvector)
   │                      account-specific ones → bounded agent loop
   │                      billing → documents first, agent for what they miss
   │
   ├─ answer ───────────  grounded strictly in retrieved passages; citations
   │                      are rebuilt from retrieval, never taken from the model
   │
   ├─ policy ───────────  deterministic rules decide escalation and refusal —
   │                      not the model's opinion of its own confidence
   │
   └─ record ───────────  trace, token cost, review item, conversation turn
```

Answers reach the client over HTTP or a WebSocket. Both run the **same
pipeline**, so a streamed answer is as grounded as a fetched one.

---

## Engineering decisions worth the words

**Every external dependency has a deterministic in-process twin.** No database,
no Redis, no provider key — the service still starts, serves, and reports
precisely which components are unconfigured. That is why the test suite runs
offline and a fresh clone runs at all.

**Citations are stored as snapshots, not foreign keys.** A citation is a claim
about what one answer was based on at one moment. Resolving a reference later
would show whatever the document says *now* and attribute it to an answer that
predates the edit. Provenance that changes under you is not provenance.

**Nothing migrates on boot.** Not at startup, not in the container, not on
`docker compose up`. Applying a migration is a decision about a database
somebody chose; a container that migrates on start makes that decision for them,
once per replica.

**You cannot stream a policy-checked answer token by token.** Policy can replace
a reply outright, so the pipeline completes before the first delta is sent. The
cost is time to first token; the alternative is a client watching an answer
retract itself.

**Errors report where a request was wrong, never what it contained.** A
malformed body carrying a customer's message — card number and all — comes back
as field paths only.

---

## Running it

```bash
NEXA_DB_PASSWORD=choose-anything-local docker compose up --build
```

Client on <http://127.0.0.1:15173>, API on <http://127.0.0.1:18000>. PostgreSQL
and Redis publish no ports — they exist for the backend, and a stray published
5432 beside the one you already run is how you write to the wrong database.

Migrations are explicit:

```bash
NEXA_DB_PASSWORD=... docker compose --profile migrate run --rm migrate
```

Without Docker, see [docs/development.md](docs/development.md).

---

## Tests

```bash
pytest                                    # 1518 backend
cd frontend && npm run test               # 202 frontend
```

Passes on a fresh clone with no infrastructure and no credentials. Tests needing
PostgreSQL, Redis or Docker skip themselves rather than fail. The suite blocks
outbound connections, so no test can reach a real provider — a guard that has
twice caught code trying to.

To run the gated ones:

```bash
createdb nexaassist_test && pytest
```

---

## Repository layout

| Path | Contents |
| --- | --- |
| `backend/` | FastAPI service — agent loop, retrieval, policy, realtime, observability |
| `frontend/` | React + TypeScript client; tests sit beside the components they cover |
| `tests/` | Backend suite. `db/`, `redis/`, `docker/` need real infrastructure |
| `docs/` | [overview](docs/overview.md) · [architecture](docs/architecture.md) · [API](docs/api.md) · [development](docs/development.md) · [milestones](docs/milestones.md) |
| `.github/` | Fast suites on every push; a second workflow for PostgreSQL, Redis and a built stack |
| `scripts/` | `scan-secrets.sh`, run in CI before every push |

---

## Stack

Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL 15+ with pgvector ·
Alembic · Redis · Groq · fastembed · React 18 · TypeScript · Vite · Docker ·
GitHub Actions

Deployed on Vercel (client), Render (API) and Neon (PostgreSQL).

---

## Known limitations

- The free-tier instance runs the **lexical** embedder, which ranks correctly but
  needs shared vocabulary between question and document. Semantic retrieval needs
  more memory than 512 MB allows.
- **Not multi-tenant.** Authorization scopes resources to a subject, which is not
  the same thing.
- **Not a ticketing system of record.** It models tickets to act on them.
