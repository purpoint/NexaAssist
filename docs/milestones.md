# Milestones

Each milestone is a self-contained change set: code, docs, and tests together.
Scope is intentionally narrow so nothing is built speculatively.

## M0 — Repository Foundation ✅

- Directory structure for backend, frontend, docs, and tests
- `.gitignore`, `.env.example`, root `README.md`
- FastAPI application factory with a single `/api/health` endpoint
  _(superseded by `/api/v1/health` in M1, and removed after M21 once a real
  consumer existed and did not use it)_
- Environment-driven settings
- React + TypeScript project scaffolding (structure and config only, no UI)
- Backend test covering the health endpoint

## M1 — FastAPI Foundation ✅

- [x] API versioning: v1 router tree mounted at `/api/v1`
- [x] Health endpoint moved to `app/api/v1/health.py`
- [x] `/api/health` preserved as a deprecated, config-gated alias
      _(removed after M21; see the note under M0)_
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

## M8 — Intent Router ✅

Delivered in three checkpoints.

- [x] Handler protocol and an explicit intent-to-handler registry that refuses
      to start with an unmapped category
- [x] Fallback and ambiguity handling: three distinct reasons — no category,
      low confidence, no handler — sharing one destination but never one label
- [x] Concrete handlers over existing capabilities: the M5 knowledge base for
      documented answers, the M7 agent for account-state questions, and a
      fallback that declines to guess

## M9 — Specialized Workflows ✅

Delivered in three checkpoints.

- [x] Workflow definition format: declarative, immutable, validated — declared
      inputs, ordered steps, and whole-value references to earlier output, with
      forward and self references rejected at construction
- [x] Workflow execution over the M6 executor, so every step inherits validated
      parameters, bounded time, and failures returned rather than raised
- [x] A library of concrete workflows over the same tools the agent uses

Workflows are the deterministic counterpart to the M7 agent, for cases where
the right steps are already known — not a replacement for it.

## M10 — Deterministic Policy Engine ✅

Delivered in three checkpoints.

- [x] Policy rules evaluated outside the model: pure functions over the message,
      its classification, and the proposed reply — no provider call, no
      database, no clock, so the same input always yields the same decision
- [x] Policy precedence over model output: policy runs *after* the handler, on
      what would actually be sent, and the model cannot overrule it
- [x] A shipped rule set — no financial commitments, complaints to a human,
      unresolved requests not claimed as answered — wired into routing

## M11 — Human-in-the-Loop ✅

Delivered in three checkpoints.

- [x] Escalation criteria: deterministic, evaluated outside the model, with
      reasons that accumulate rather than short-circuit
- [x] Human review queue: `review_items`, with claiming made race-safe by
      putting the status check inside the UPDATE
- [x] Handoff: escalation applied after routing and policy, telling the
      customer a person is involved — and never costing them a reply when the
      queue write fails

## M12 — Conversation Memory ✅

Delivered in three checkpoints.

- [x] Conversation state and history: `conversations` and
      `conversation_messages`, with an explicit `position` rather than an order
      inferred from timestamps
- [x] Conversation service assigning positions and storing a token estimate
      alongside each turn
- [x] Context window management: recency-first selection that never exceeds the
      budget, truncating the newest turn rather than dropping the question

## M13 — Redis + Background Jobs ✅

Delivered in three checkpoints.

- [x] Job queue interface and a deterministic in-memory backend: a `JobQueue`
      protocol with validation shared by every implementation, so a payload
      that works offline cannot fail against a server
- [x] Redis integration: a second backend behind the same protocol, durable
      and shared, with at-least-once handoff via `LMOVE` and every key confined
      to one namespace
- [x] Background job execution: an explicit handler registry, a worker that
      lets no handler exception escape, and concrete handlers over the existing
      document and ticket services

No HTTP surface: M13 specifies none, and the OpenAPI schema is unchanged.
Live Redis tests skip when no server is reachable, as the database tests do.

## M14 — WebSockets + Realtime ✅

Delivered in three checkpoints.

- [x] WebSocket transport: `GET /api/v1/ws`, a typed wire contract that rejects
      unknown frames at the edge, and a connection registry with a ceiling
- [x] Streaming responses: a `StreamingLLMProvider` protocol separate from
      `LLMProvider`, with a real Groq implementation and a deterministic
      offline one
- [x] Streamed answers over the socket: `ask` produces ordered `delta` frames
      and one `complete`, one stream at a time per connection, with every
      failure reported as a frame rather than raised

No OpenAPI change: FastAPI describes HTTP operations, and a WebSocket route is
not one — so the contract is stated in `app/realtime/envelope.py` and pinned by
tests instead. Not the grounded answer path; citations cannot be streamed.

## M15 — Evaluation Framework ✅

Delivered in three checkpoints.

- [x] Cases, checks, and reports: expectations rather than recorded outputs,
      checks that are pure functions, and a case that passes only when every
      check did
- [x] The harness: runs a suite against an `EvalTarget`, lets nothing escape,
      keeps a failed target and a broken check distinct, and compares two runs
      to name what stopped passing
- [x] The shipped regression suite: the deterministic policy and escalation
      layers evaluated end to end, plus prompt digests pinned to their versions

Deliberately no offline suite over model output: the static provider returns one
canned response per schema, so such a suite would pass whatever the prompt said.
The model-facing guard is the digest pin; judging the model needs a real
provider and is an operator action.

## M16 — Observability + Cost Tracking ✅

Delivered in three checkpoints.

- [x] Structured tracing: spans correlated through a `ContextVar`, three
      recorders behind one protocol, and attribute values that cannot carry
      prose
- [x] Token and cost accounting: provider/model-aware usage, deterministic
      `Decimal` cost from a configured price list, and no invented prices
- [x] Integration across the agent, tools, workflows, routing, and LLM calls —
      by wrapping protocols at the composition root, so no earlier milestone's
      source changed

No HTTP surface and no dependencies; OpenAPI unchanged.

## M17 — Production Assistant API ✅

Delivered in three checkpoints.

- [x] `POST /api/v1/assistant/messages`: the first HTTP surface over the
      answering pipeline, with the classify → route → escalate order stated
      once in `AssistantService`
- [x] Conversation integration: `POST /api/v1/conversations`,
      `GET /api/v1/conversations/{id}/messages`, and an optional
      `conversation_id` on a message
- [x] Hardening: a pinned path list, concurrency, error mapping, readiness
      behaviour, and regression against M1–M16

_Retitled during this milestone. The roadmap previously scheduled Security
Hardening here, but M6–M16 had shipped no endpoint over the pipeline they
built, so an application surface had to come first. **Authentication,
authorization, and tenant isolation are deferred, not dropped** — nothing in
M17 adds auth, and the API is unauthenticated until that milestone lands.

## M18 — Frontend Integration Readiness ✅

Delivered in three checkpoints.

- [x] The frontend-facing contract: citations carried from retrieval all the
      way to the assistant response, and dropped whenever policy rewrote the
      reply
- [x] Flow integration: one conversation shared by the HTTP endpoint and the
      M14 socket, plus `GET /api/v1/conversations/{id}`
- [x] Hardening: the realtime frame vocabulary pinned, response shapes checked
      for consistency across both transports, CORS verified, and the error
      contract held to one shape

_Scoped to the backend contract: this milestone makes the backend consumable
rather than consuming it, and installed no frontend dependency. The **React
application was deferred out of it and shipped as M21**._

## M19 — Production Hardening ✅

Delivered in three checkpoints. This is the security hardening the roadmap
first scheduled for M17 and then deferred; it is no longer a separate
milestone.

- [x] Authentication and request identity: an `Authenticator` protocol with a
      shared-key implementation and an anonymous one, a single `RequestIdentity`
      type rather than `Identity | None`, and refusals that never distinguish a
      missing credential from a wrong one
- [x] Authorization and resource ownership: an `OwnerScope` holding the one
      definition of "owns", ownership on conversations and tickets, and another
      subject's resource returning the same 404 as one that does not exist
- [x] Rate limiting: a backend-neutral `RateLimiter` with none/memory/redis
      implementations, keyed by the authenticated subject, reusing the existing
      Redis configuration

**Both authentication and authorization are off by default**, so a deployment
that has not opted in behaves exactly as it did before. One Alembic revision
(`5aa59ba365ee`) adds a nullable `owner_subject` to `conversations` and
`tickets`.

**Known limitation:** the WebSocket carries no identity. Under scoped
authorization it refuses to record a turn rather than writing unscoped, and the
client falls back to HTTP. Authenticating the socket needs a credential
transport a browser can use — see _Outstanding_ below.

## M20 — Observability and Operations ✅

Delivered in three checkpoints, extending M16 rather than replacing it.

- [x] Application metrics: a vendor-neutral protocol, label values that cannot
      carry prose, and a per-metric cardinality cap, recorded at the seams M16
      already wraps
- [x] Operational health diagnostics: a `components` report on `/ready` covering
      the database, job queue, model provider, rate limiter and authentication,
      with `degraded` distinct from `unavailable` — only the database can make
      the service unready
- [x] Observability hardening: a trace id on every log record, configured
      credentials registered as literals to scrub, and validation errors no
      longer echoing the request

`/health` is unchanged. Validation errors returning `ErrorResponse` instead of
FastAPI's default body is a **deliberate contract change**: the default embeds
the offending input, so a malformed request returned the customer's message
back to them.

## M21 — Frontend ✅

Delivered in three checkpoints. This is the React application deferred out of
M18.

- [x] Foundation: the backend contract transcribed as TypeScript, a typed API
      client that returns data or throws one error type, an application shell,
      and a connection indicator driven by `/ready`
- [x] Assistant conversation experience: transcript, composer, citations,
      history and resumption, with optimistic sending and a failed question
      kept on screen rather than discarded
- [x] Realtime: the M14 frame vocabulary exactly, backoff that stops, streamed
      deltas, and an HTTP fallback whenever the socket cannot take a question

48 frontend tests; `tsc --noEmit` and a production build pass.

**What it is not:** there is one screen and no router, so "routing" in the
original M18 wording is unbuilt — nothing yet needs a second route.

A key entry point was added after M21: the client stores a key, sends it on
every request, and opens the panel by itself when a request comes back 401.
**The socket is still unauthenticated** — a browser cannot set a header on a
WebSocket handshake — so with authentication on, HTTP carries the key and the
socket does not. Under scoped authorization the server refuses to record a
realtime turn and the client falls back to HTTP, which works but streams
nothing. That is the remaining Outstanding item.

## M22 — Docker / Production-like Environment

- [ ] Container images
- [ ] Local production-like compose environment

## M23 — CI/CD + Deployment

- [ ] CI pipeline
- [ ] Deployment

## M24 — Documentation

- [ ] Architecture and operations documentation
- [ ] API reference

## M25 — Resume + Interview Preparation

- [ ] Project write-up
- [ ] Architecture walkthrough

## Outstanding

Carried between milestones rather than belonging to one. Listed here so they
are visible instead of buried in the milestone that last deferred them.

- [ ] **Authenticate the WebSocket.** A browser cannot set a header on a
      WebSocket handshake, so this needs a decision about the credential
      transport (a subprotocol, a first-frame handshake, or a short-lived
      ticket) before it can be built.
