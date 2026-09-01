# Milestones

Each milestone is a self-contained change set: code, docs, and tests together.
Scope is intentionally narrow so nothing is built speculatively.

## M0 — Repository Foundation ✅

- Directory structure for backend, frontend, docs, and tests
- `.gitignore`, `.env.example`, root `README.md`
- FastAPI application factory with a single `/api/health` endpoint
- Environment-driven settings
- React + TypeScript project scaffolding (structure and config only, no UI)
- Backend test covering the health endpoint

## M1 — FastAPI Foundation ✅

- [x] API versioning: v1 router tree mounted at `/api/v1`
- [x] Health endpoint moved to `app/api/v1/health.py`
- [x] `/api/health` preserved as a deprecated, config-gated alias
- [x] `AppError` foundation and consistent `ErrorResponse` error bodies
- [x] Standard-library logging configuration
- [x] Tests for v1 health, the legacy alias, app creation, and 404 handling

## M2 — LLM Foundation

Delivered in three checkpoints.

- [x] LLM configuration on `Settings`, and the provider abstraction
      (`LLMProvider` protocol, `LLMConfig`, `LLMPrompt`, `StructuredCompletion`)
- [x] Groq provider foundation and a deterministic `StaticLLMProvider`
- [x] Structured intent analysis: `IntentAnalysis`, `IntentService`, and
      `POST /api/v1/intent/analyze`
- [x] Hardened LLM error taxonomy, total request deadline, secret redaction
      in logging, and the `CORS_ORIGINS` startup fix
      _(bounded repair retry deliberately dropped: strict mode plus a clean
      6/6 live smoke test gave no evidence it was needed)_

## M3 — PostgreSQL + Alembic ✅

Delivered in three checkpoints.

- [x] Database connection layer: async engine, pooling, request-scoped
      sessions, lifecycle, and database errors on the shared envelope
- [x] Alembic migrations: async `env.py` sourcing its URL from `Settings`,
      baseline revision, and a proven upgrade/downgrade round trip
- [x] Persistence models: declarative `Base`, constraint naming convention,
      and `TimestampMixin`
- [x] `GET /api/v1/ready` reporting real database connectivity
      _(no business tables — those arrive with M4)_

## M4 — Core Business Domain ✅

Delivered in three checkpoints.

- [x] First domain resource end to end: `POST/GET /api/v1/tickets`
- [x] Domain models: `Customer` and `Ticket`, with constraints, indexes, and
      an Alembic migration
- [x] Service layer: `TicketService` owning the transaction boundary, depending
      on a session and neither FastAPI nor a provider SDK

## M5 — RAG + pgvector ✅

Delivered in three checkpoints.

- [x] Document ingestion: chunking, embedding, and storage in one transaction
- [x] pgvector storage and retrieval: `vector(384)` column, HNSW index with the
      cosine operator class, ranking performed in PostgreSQL
- [x] Grounded answers with citations: `POST /api/v1/documents/answer`, which
      answers only from retrieved sources and rebuilds citations from
      retrieval rather than trusting the model

## M6 — Tool System ✅

Delivered in three checkpoints.

- [x] Tool interface and registry: a `Tool` protocol whose parameters are a
      Pydantic model (validation and JSON Schema from one definition), and an
      explicit registry that rejects duplicate or malformed registrations
- [x] Tool execution and result handling: an executor that validates, bounds
      the call in time, and converts every failure into a `ToolResult` — no
      exception escapes and no internals reach the caller
- [x] Domain tools over the existing model: `lookup_ticket`, `list_tickets`,
      `search_knowledge_base`

No HTTP surface: M6 specifies none, and the agent loop that drives these
belongs to M7.

## M7 — Agent Core ✅

Delivered in three checkpoints.

- [x] Agent state and step accounting: every step recorded, with a two-part
      budget (steps and tool calls) so neither can run away
- [x] Agent loop over the tool system: decide, act, observe, repeat — every
      failure ends the run with an answer rather than an exception
- [x] Composition over the M6 domain tools, with the budget read from settings

No HTTP surface: the roadmap specifies none, and choosing *which* agent handles
a request is routing, which is M8.

## M8 — Intent Router

- [ ] Route classified intents to handlers
- [ ] Fallback and ambiguity handling

## M9 — Specialized Workflows

- [ ] Workflow definition format
- [ ] Workflow execution

## M10 — Deterministic Policy Engine

- [ ] Policy rules evaluated outside the model
- [ ] Policy precedence over model output

## M11 — Human-in-the-Loop

- [ ] Escalation criteria
- [ ] Handoff and human review queue

## M12 — Conversation Memory

- [ ] Conversation state and history
- [ ] Context window management

## M13 — Redis + Background Jobs

- [ ] Redis integration
- [ ] Background job execution

## M14 — WebSockets + Realtime

- [ ] WebSocket transport
- [ ] Streaming responses

## M15 — Evaluation Framework

- [ ] Prompt and workflow evaluation harness
- [ ] Regression suite for model-facing changes

## M16 — Observability + Cost Tracking

- [ ] Structured tracing
- [ ] Token and cost accounting

## M17 — Security Hardening

- [ ] Authentication
- [ ] Authorization and tenant isolation

## M18 — Production Frontend

- [ ] Remove the deprecated `/api/health` alias and `ENABLE_LEGACY_HEALTH_ROUTE`
      _(originally scheduled for M2; retargeted here, the first milestone with a
      real API consumer)_
- [ ] Install frontend dependencies
- [ ] Application shell and routing
- [ ] Typed API client wired to the backend

## M19 — Docker / Production-like Environment

- [ ] Container images
- [ ] Local production-like compose environment

## M20 — CI/CD + Deployment

- [ ] CI pipeline
- [ ] Deployment

## M21 — Documentation

- [ ] Architecture and operations documentation
- [ ] API reference

## M22 — Resume + Interview Preparation

- [ ] Project write-up
- [ ] Architecture walkthrough
