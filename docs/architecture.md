# Architecture

> Placeholder — records the intended shape of the system. Nothing below the
> "Current state" section is implemented yet.

## Current state

```
Client (React + TS)  ──HTTP──▶  FastAPI service
                                  ├── GET  /api/v1/health
                                  ├── GET  /api/v1/ready
                                  ├── POST /api/v1/intent/analyze
                                  ├── POST /api/v1/documents
                                  ├── POST /api/v1/documents/answer
                                  ├── POST /api/v1/tickets
                                  ├── GET  /api/v1/tickets
                                  ├── GET  /api/v1/tickets/{id}
                                  └── GET  /api/health   (deprecated alias)
```

That is the entire runtime today: one HTTP service exposing a health check, and
a frontend shell that does not yet call it.

## API versioning

Routes are versioned in the router tree, not in path strings. Every v1 module
lives in `app/api/v1/`, is aggregated by `app/api/v1/router.py`, and is mounted
once in `main.py` under `Settings.api_v1_prefix` (`/api` + `/v1`). No module
hardcodes the version segment.

A breaking change to a published route means adding an `app/api/v2/` package
and running both versions, not editing v1 in place.

### Deprecation: `/api/health`

The pre-versioning `/api/health` endpoint from milestone M0 is still served. It
shares the same router as `/api/v1/health`, so the two are behaviourally
identical, and a test asserts that they stay identical.

- It is flagged `deprecated: true` in the OpenAPI schema and renders
  struck-through in `/docs`.
- It is gated by `ENABLE_LEGACY_HEALTH_ROUTE` (default `true`). Set it to
  `false` to confirm nothing still depends on it.
- **It is removed in M18**, along with the setting and its tests. (M1 named
  M2 for this; M2 became the LLM foundation, so removal moved to the first
  milestone with a real API consumer rather than being dropped silently.)

## Error handling

Every error leaves the API in one shape, `app.schemas.common.ErrorResponse`:

```json
{ "code": "not_found", "message": "Not Found", "details": null }
```

`details` is omitted when empty. Handlers registered by
`app.core.exceptions.register_exception_handlers` cover two cases: `AppError`
subclasses raised deliberately by application code, and Starlette's
`HTTPException` (which is what an unmatched route raises). Request-validation
errors keep FastAPI's default 422 body until an endpoint actually accepts a
request body.

## Logging

Configured once per process in `app.core.logging.configure_logging`, called
from the application factory. Standard library `dictConfig` only; uvicorn's
loggers propagate to the same root handler so every line in the process shares
one format and level. Level comes from `LOG_LEVEL`.

## Design principles

1. **Layered backend.** Routes stay thin; domain logic lives in services. This
   keeps the API surface swappable and the logic testable in isolation.
2. **Configuration via environment.** No environment-specific values in code.
3. **Explicit boundaries.** Anything crossing a process boundary (HTTP, model
   provider, datastore) gets a typed schema.
4. **Incremental.** Each capability lands as a milestone with its own docs and
   tests, rather than a large speculative framework up front.

## Backend module map

| Package | Responsibility |
| --- | --- |
| `app.main` | Application factory: creates the FastAPI app, applies middleware and error handlers, mounts routers, defines lifespan. |
| `app.core.config` | Settings loaded from environment, validated once at startup. |
| `app.core.exceptions` | `AppError` hierarchy and the handlers that render it. |
| `app.core.logging` | Process-wide logging configuration. |
| `app.llm` | Language-model access: the provider contract and its implementations. |
| `app.db` | Connection pool, session lifecycle, and the declarative foundation. |
| `app.api.v1` | Version 1 HTTP endpoints. One module per resource. |
| `app.schemas` | Request/response models — the API contract. `common.py` holds shapes used by more than one endpoint. |
| `app.models` | Persistence models. Empty until a database is introduced. |
| `app.services` | Domain logic and orchestration. Empty until workflows are introduced. |

## Language-model layer

`app/llm/` is the only part of the codebase that knows a model vendor exists.

```
Application  ->  LLMProvider (protocol)  ->  GroqProvider  ->  Groq API

llm/
├── base.py       the vendor-neutral contract: LLMProvider, LLMConfig,
│                 LLMPrompt, StructuredCompletion
├── errors.py     LLM failures as AppError subclasses
├── factory.py    name -> implementation registry, and the DI entry point
├── prompts.py    prompt text, versioned as named constants
└── providers/    concrete implementations (groq, static)
```

Callers depend on `base` only. They receive a provider through
`Depends(get_llm_provider)`, never by importing a provider module, so swapping
or adding a backend touches no call site.

Design rules:

- **`LLMProvider` is a `Protocol`, not a base class.** Implementations neither
  import nor inherit from it, so a test double or a future third-party adapter
  conforms structurally.
- **Providers are stateless and configured per call.** No conversation memory
  lives inside a provider; that belongs to an orchestration layer.
- **`LLMConfig` is narrow.** A provider never receives the application
  `Settings`, so provider tests construct a config directly instead of
  populating an environment.
- **Two implementations from the start.** `StaticLLMProvider` is deterministic
  and offline — it runs the app without credentials and keeps the abstraction
  honest, since a single-implementation interface has not been shown to be an
  interface.
- **`app.core` never imports `app.llm`.** The projection from `Settings` to
  `LLMConfig` lives in `llm/factory.py` so the layering runs one way.

### Structured output

Groq exposes structured output the OpenAI-compatible way: the request carries a
JSON Schema in `response_format`, and the reply is a JSON string that the
provider validates against the caller's Pydantic model. The SDK ships no
`parse()` helper, so `GroqProvider` assembles this itself.

`strict: true` (constrained decoding) is used, which Groq requires be paired
with a schema where every object sets `additionalProperties: false` and lists
all of its properties in `required`. Pydantic emits neither, so
`strict_json_schema()` adds both at every depth rather than having each schema
hand-write them.

Structured output is **model-dependent**. As of writing, strict mode is
supported on `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, and
`qwen/qwen3.8-27b`; the default `LLM_MODEL` is `openai/gpt-oss-120b`. Models
outside that set — `llama-3.3-70b-versatile`, for instance — support only JSON
object mode and will not honour a schema. Changing `LLM_MODEL` means checking
that list first.

`LLM_TEMPERATURE` is optional and provider-agnostic. It defaults to unset and
is omitted from the request entirely when unset.

### Credentials

The API key is read from `GROQ_API_KEY` — the same variable the Groq SDK reads,
so an already-exported key needs no `.env` entry. It is typed `SecretStr`, so
it renders as `**********` in any repr, and it is never placed in a log line, an
error body, or `.env.example`. Leaving it blank means *unset*, which lets the
SDK resolve it from the environment.

### Error handling

No provider exception escapes `app/llm/providers/`. Each SDK failure is
translated into an `LLMError` subclass carrying a fixed status, a stable code,
and a **generic** message — clients learn the category and nothing about our
vendor, credentials, or request shape.

| Provider failure | Application error | HTTP |
| --- | --- | --- |
| total deadline exceeded (backoff included) | `LLMTimeoutError` | 504 |
| `APITimeoutError` (single attempt) | `LLMTimeoutError` | 504 |
| `RateLimitError` | `LLMRateLimitError` | 429 + `Retry-After` |
| `AuthenticationError`, `PermissionDeniedError`, `NotFoundError` | `LLMConfigurationError` | 500 |
| `BadRequestError`, `UnprocessableEntityError` | `LLMRequestError` | 500 |
| `APIStatusError` ≥ 500 | `LLMUnavailableError` | 503 |
| `APIConnectionError` | `LLMUnavailableError` | 503 |
| any other `APIError` | `LLMError` | 500 |
| construction failure (`GroqError`, no key) | `LLMConfigurationError` | 500 |
| empty content or failed Pydantic validation | `LLMInvalidOutputError` | 500 |

**Upstream status codes are not our status codes.** A provider 401 means *we*
misconfigured a key; the caller authenticated fine, so returning 401 would send
them debugging their own credentials. Only genuinely retryable conditions —
rate limiting, outage, timeout — pass a retryable status through.

Catch clauses run most-specific-first, which the SDK hierarchy makes
load-bearing: `APITimeoutError` is an `APIConnectionError`, and `RateLimitError`
and friends are `APIStatusError`s. Tests pin this ordering.

`details` is a fixed whitelist — `provider`, `model`, and `retry_after_seconds`
— never `repr(exc)`. That matters more than log hygiene, because `details` is
rendered into the HTTP response body.

Invalid model output raises rather than being repaired or defaulted. There is
no repair retry: a fabricated result that looks real is worse than a loud
failure, and a silent second call would double latency and cost.

### Request deadline

`GroqProvider` wraps the whole SDK call in `asyncio.timeout`
(`LLM_TOTAL_TIMEOUT_SECONDS`, default 90s). The bound belongs to the provider,
not the service: the SDK retries and sleeps for backoff *inside* a single
await, so only the provider can see the real cost of a call, and the service
holds no `LLMConfig` to bound it with.

The distinction is not academic. `LLM_TIMEOUT_SECONDS` bounds one attempt; a
real smoke-test call took **34.8s** after a server-directed `retry-after`.
Bounding only an attempt would not have bounded the request. A validator
rejects a total smaller than the per-attempt timeout at startup.

### Secret redaction

`SecretRedactingFilter` is attached to the console handler, so it covers every
logger in the process — ours, the provider SDK's, and the HTTP client's. It
scrubs `gsk_` tokens, `Bearer` values, `*api[_-]?key`/`authorization`
assignments, and any literal registered at startup (the configured key).

Redaction runs on the *formatted* message: a secret passed as a `%s` argument
never appears in `record.msg`, so filtering the template alone would miss it.

It is a backstop, not the control. Nothing is supposed to log a credential, a
prompt, or model output in the first place — `IntentService` logs only
provider, model, prompt version, intent, latency, and token counts, and tests
assert the customer message and the model's `reason` never appear.

## Intent analysis

The first capability that actually calls a model. The chain is one-way, and
each link depends only on the one below it:

```
POST /api/v1/intent/analyze
  └─ api/v1/intent.py      validate request, call service, return  (no logic)
      └─ IntentService     compose prompt + schema, unwrap result
          └─ LLMProvider   protocol -- the service knows nothing else
              └─ GroqProvider     one of two modules importing the SDK
                  └─ Groq structured JSON output
                      └─ IntentAnalysis (validated by Pydantic)
                          └─ API response
```

`IntentAnalysis` is deliberately one model doing two jobs: it is the JSON
Schema sent to the provider *and* the HTTP response model, so the wire contract
and the model contract cannot drift apart.

The category set is closed (`billing`, `technical_support`, `account`,
`product_question`, `complaint`, `other`) so that M8's router can dispatch on a
lookup rather than fuzzy matching. `other` is mandatory: without an escape
hatch the model is forced to miscategorise.

`confidence` is the model's self-report, **not** a calibrated probability --
see `docs/prompt.md`.

Nothing here decides what the business should *do* with a classification.
Urgency, escalation, prioritisation, and human review belong to M10 and M11,
and are absent from both the schema and the prompt on purpose.

### Running it without a Groq key

`LLM_PROVIDER=static` serves this endpoint from `StaticLLMProvider`. The
factory -- the composition root -- supplies the canned catalogue
(`IntentAnalysis -> STATIC_EXAMPLE`), so the provider module itself stays free
of domain vocabulary. The canned answer is `other` at zero confidence, so it is
obvious no model was involved.

## Persistence

PostgreSQL, reached through SQLAlchemy 2.0's async layer over `asyncpg`.

```
Request
  └─ Depends(get_db_session) ──▶ AsyncSession (one per request)
                                   └─ async_sessionmaker
                                        └─ AsyncEngine (pooled, app lifetime)
                                             └─ asyncpg ──▶ PostgreSQL
```

Rules this layer holds to:

- **The engine is built lazily and disposed at shutdown.** Constructing it opens
  no connection, so an unreachable database does not stop the service starting
  — the same tolerance that lets it run without a provider key.
- **Sessions never commit implicitly.** A caller that writes says so. An
  auto-committing dependency would bake a persistence policy into the framework
  layer before any writer exists.
- **`create_all()` is never called, and nothing migrates at startup.** The
  schema is owned by migrations alone; two mechanisms claiming that ownership is
  how environments drift.
- **The async driver is mandatory.** A `postgresql://` URL is rejected during
  settings validation, because against an async engine it otherwise fails at
  connect time with a message that points nowhere useful.
- **`DATABASE_URL` is a secret.** It carries a password outside local
  development, so it is a `SecretStr`, is registered with the log redactor, and
  a URL-credential pattern masks any that reaches a log line from elsewhere.
  Error bodies never contain a connection string, host, or user.

### Constraint naming

`Base.metadata` carries an explicit naming convention, settled deliberately
before the first table exists. PostgreSQL invents constraint names when none is
given, and Alembic cannot reliably emit `DROP CONSTRAINT` for a name it does not
know — so a later migration that alters a constraint fails or diverges between
databases. Adopting the convention afterwards would mean a rename migration for
every table already shipped.

M3 ships no business tables. Tickets, conversations, and customers belong to M4;
this milestone builds the road rather than driving on it.

### Readiness

`GET /api/v1/ready` is deliberately separate from `GET /api/v1/health`, and the
health contract is unchanged.

| | Question | Behaviour when the database is down |
| --- | --- | --- |
| `/health` | Is this process alive? | **still 200** |
| `/ready` | Should traffic come here? | **503** |

Conflating the two is a well-known way to make an outage worse: if liveness
follows readiness, an orchestrator restarts a perfectly healthy process because
a dependency blipped, and the restart storm outlasts the original fault.

The probe performs a real `SELECT 1` rather than inspecting the pool — a pooled
connection can look healthy while the server behind it is gone.

An unreachable database yields 503 through the standard `ErrorResponse`, so a
load balancer can act on the status code without parsing a body. A database
that was never configured is reported as `not_configured` with a 200: running
without one is a supported mode, and the body says so plainly rather than
pretending the service is broken.

Error bodies from this path carry `{"component": "database"}` and nothing else.
Driver messages routinely contain the host, the user, and sometimes the
connection string, so none of them reaches a response or a log line — only the
exception type is logged.

### Domain model

M4 introduces the first business tables.

```
customers                         tickets
  id          uuid pk               id           uuid pk
  email       varchar(320) uq       customer_id  uuid fk -> customers.id  RESTRICT
  created_at  timestamptz           subject      varchar(200)
  updated_at  timestamptz           body         text
                                    status       varchar(20)  open|pending|resolved|closed
        1 ────────< many            created_at   timestamptz
                                    updated_at   timestamptz
```

Choices worth knowing:

- **UUID primary keys, generated by the database.** These identifiers travel in
  URLs. A sequence would disclose how many tickets exist and invite reading a
  neighbour's by decrementing the number.
- **`status` is `VARCHAR` + `CHECK`, not a PostgreSQL `ENUM`.** Native enums
  need `ALTER TYPE ... ADD VALUE` to extend and are close to impossible to
  shrink; a check constraint is a one-line migration. Which *transitions* are
  legal is policy, and belongs to a later milestone.
- **`ON DELETE RESTRICT`.** Removing a customer must never quietly destroy
  their support history. There is no delete endpoint, so this governs direct
  database work — where a safe default matters most.
- **Check constraints duplicate what the API already validates.** The API is
  not the only writer: migrations, scripts, and later a worker reach the same
  tables. The database is the one layer that cannot be bypassed.
- **`customers` carries an identity and nothing else.** The entity earns its
  table by being unique and a foreign-key target, not by accumulating fields.

### Relationships and lazy loading

Both relationships are declared `lazy="raise"`. Under asyncio an accidental
lazy load surfaces as `MissingGreenlet` at an arbitrary point far from its
cause — or worse, works in a synchronous test and fails in production.
`lazy="raise"` turns it into an immediate error at the access site, which
forces callers to eager-load deliberately with `selectinload`. Every later
relationship inherits this default.

### Indexes

Each index maps to a query the API issues; none is speculative.

| Index | Serves |
| --- | --- |
| `ix_tickets_customer_id` | foreign-key lookups, "tickets for this customer" |
| `ix_tickets_created_at` | the default listing, newest first |
| `ix_tickets_status_created_at` | the status-filtered listing |

The composite is named explicitly: the project convention derives an index name
from its first column alone, which would collide with a plain status index.

### Ticket flow

```
POST /api/v1/tickets
  └─ api/v1/tickets.py   validate, delegate, return   (no logic)
      └─ TicketService   get-or-create customer, insert, commit
          └─ AsyncSession (request-scoped, no implicit commit)
              └─ PostgreSQL
```

The service owns the transaction boundary because only it knows what one
complete business operation is — here, "a ticket exists, along with the
customer it belongs to". Get-or-create races on the unique email constraint;
the insert runs inside a SAVEPOINT so the loser rolls back one statement rather
than the whole request.

Listings order by `created_at DESC, id DESC`. Timestamps collide at this
resolution, and an unstable sort makes pagination skip or repeat rows.

Responses carry `customer_id`, never the customer object: the relationship is
`lazy="raise"` and nothing in the contract needs it.

### Retrieval (M5)

```
POST /api/v1/documents/answer
  └─ AnswerService
      ├─ DocumentService.search  ── embed query ─▶ ORDER BY embedding <=> query
      │                                            (HNSW, vector_cosine_ops)
      └─ LLMProvider             ── answer strictly from the retrieved spans
          └─ citations rebuilt from retrieval, not from the model
```

- **Ranking happens in PostgreSQL.** Pulling rows into Python to sort would
  bypass the HNSW index entirely.
- **Cosine, not L2.** The embedding model returns unit-length vectors, so the
  cosine operator class is the one that matches them.
- **Embedding width is schema, not settings.** Changing models is a migration;
  a mismatch is rejected by the database rather than yielding meaningless
  distances.
- **No sources means no model call.** Answering anyway would produce the
  ungrounded response the endpoint exists to avoid.
- **Citations are rebuilt from retrieval.** The model reports which source
  numbers it used; titles, ids, and excerpts come from the retrieved rows, so a
  hallucinated citation cannot reach a reader.

### Tool system (M6)

```
caller ─▶ ToolExecutor ─▶ ToolRegistry ─▶ Tool ─▶ Service ─▶ DB / retrieval
                 └────────▶ ToolResult (always; never an exception)
```

- **Parameters are a Pydantic model**, so validation and the JSON Schema a
  model-driven caller reads come from one definition rather than two that
  drift.
- **The executor never raises.** A tool is typically invoked on behalf of a
  model, which cannot catch anything; an escaping exception would abort a whole
  turn. Every outcome — unknown tool, invalid parameters, deliberate failure,
  timeout, crash — is a `ToolResult`.
- **Unexpected failures are reported generically.** The `error` field can
  travel into a prompt and onward to a user, so driver messages, tracebacks,
  and connection strings never reach it; only the exception type is logged.
- **Registration is explicit.** What the system can be asked to *do* is visible
  in one list, rather than depending on which modules happened to be imported.
- **Tools return plain data.** A lazily-loaded ORM relationship serialised into
  a prompt would raise at the worst possible moment.

M6 adds no HTTP endpoints; the loop that chooses tools is M7.

### Agent core (M7)

```
AgentLoop.run(question)
  ├─ decide   LLMProvider -> AgentDecision (call a tool, or answer)
  ├─ act      ToolExecutor -> ToolResult   (never raises)
  ├─ observe  result folded into the next prompt
  └─ repeat until an answer, or the budget stops it
```

- **Two budgets, not one.** A loop can burn steps without calling a tool, and
  can call tools more often than it takes steps; bounding only one leaves the
  other unbounded.
- **Every failure ends with an answer.** A bad tool name, invalid parameters, a
  timeout, or a provider outage produces a reply, not a traceback — a support
  request that dies with a stack trace helps nobody.
- **Observations are fed back.** Without that the loop would call the same tool
  forever.
- **The transcript excludes tool output.** It may be logged or returned, and
  outputs carry customer content; only names, outcomes, and timings appear.

### Intent routing (M8)

```
IntentAnalysis ─▶ IntentRouter.decide ─▶ handler
                        │
                        ├─ other            ─┐
                        ├─ low confidence   ─┼─▶ FallbackHandler (declines to guess)
                        └─ no handler       ─┘
```

| Intent | Handler | Why |
| --- | --- | --- |
| `product_question`, `account` | knowledge base | the answer is documented |
| `billing`, `technical_support`, `complaint` | agent | needs to inspect account state |
| `other` | fallback | no category fits |

- **Three fallback reasons, one destination.** They must stay distinguishable
  in logs: "the classifier keeps saying other" and "we never wired that
  category" are different problems with the same symptom.
- **`require_complete()` runs at wiring time.** An unmapped category silently
  becomes fallback traffic, which reads as classifier drift rather than a
  missing route.
- **Low confidence means the model is not consulted at all.** Acting anyway is
  exactly the guess the threshold exists to reject. Confidence is self-reported,
  not calibrated, so the threshold is a coarse guard.
- **`decide()` is separate from `route()`**, so the choice can be inspected and
  tested without running a handler.

### Workflows (M9)

```
Workflow (data)        WorkflowRunner            ToolExecutor
  inputs: [...]   ──▶   resolve references  ──▶   validated, timed call
  steps:  [...]         record each step          ToolResult (never raises)
```

- **Definitions are inert data.** They can be read, validated, and compared
  without running anything; everything variable is a reference resolved at
  execution time rather than code embedded in the definition.
- **Inputs are declared.** A reference to an input is then validated exactly
  like a reference to a step, and a workflow documents what it needs.
- **References are whole-value only.** Interpolating a structured result into a
  larger string would force a stringification whose format nothing has agreed
  on.
- **Forward and self references are rejected at construction.** A workflow is a
  sequence, not a graph; a cycle could never terminate.
- **Optional steps may fail without ending the run**, but a later step that
  references a skipped one fails on its own parameters — a recorded outcome,
  never a crash.
- **The run summary excludes step outputs**, which are returned separately, so
  the object that is safe to log and the object holding customer data are not
  the same one.
- **Workflows and the agent share one tool registry**, so they cannot disagree
  about what a tool does.

### Policy (M10)

```
handler reply ──▶ PolicyEngine (ordered rules) ──▶ PolicyEnforcer ──▶ sent reply
                        first non-allow wins        model cannot overrule
```

- **Policy runs after generation, not before.** Asking a model to respect a
  rule makes compliance a suggestion; applying the rule to the finished reply
  makes it a guarantee.
- **Rules are pure.** No provider, database, clock, or randomness — the value of
  a policy engine is that a decision is reproducible and explainable without
  replaying a generation. A test asserts the modules import none of those.
- **Ordering is precedence**, and it is explicit. The first non-`allow` outcome
  wins; money is checked first because it is the costliest thing to get wrong.
- **A block overrides the handler's own verdict.** `handled` becomes false
  regardless of what the handler believed, because a blocked reply resolved
  nothing.
- **The original reply is always retained** alongside the enforced one, so a
  decision can be audited after the fact.

### Human-in-the-loop (M11)

```
routed reply ─▶ EscalationCriteria ─▶ HandoffService ─▶ review_items (pending)
                (deterministic)          notice appended to the reply
```

- **Reasons accumulate.** "Unresolved *and* a complaint" reads very differently
  in a queue from either alone, and the mix of reasons is how you tell an
  underperforming classifier from an overcautious policy.
- **`other` is not also counted as low confidence.** One situation, one reason;
  double counting would distort triage.
- **Claiming is race-safe.** The status check lives inside the `UPDATE`, so two
  reviewers opening the queue together cannot both take the same item.
- **A queue failure never costs the customer their reply.** Losing the entry is
  recoverable; dropping the reply is not. The failure is logged by exception
  type only and the reply still goes out, with `queued=False` recording it.
- **`review_items.ticket_id` is `SET NULL`**, not cascade: losing a ticket must
  not erase the record that a human was asked to look at something.

### Conversation memory (M12)

```
conversations 1 ──< conversation_messages (position, role, content, token_estimate)
                              │
                              ▼
                        ContextWindow ──▶ newest turns that fit the budget
```

- **`position` is explicit**, not inferred from `created_at`. Timestamps collide
  at this resolution, and an exchange whose order depends on a tie-break will
  eventually replay wrongly. Unique on `(conversation_id, position)`.
- **The token estimate is stored, not recomputed.** Trimming a window on every
  turn should not mean re-tokenising the whole history. It is deliberately
  conservative: under-counting costs a failed request, over-counting costs a
  little context.
- **Recency wins.** The window is filled from the newest turn backwards, then
  restored to reading order.
- **The budget is never exceeded, even by one message.** A single turn larger
  than the whole budget is truncated rather than dropped — answering the rest
  of the exchange while discarding the actual question is worse than answering
  a shortened version of it. Only the newest turn is ever truncated; an older
  one is dropped whole, because a fragment of old context is worth less than
  the space it costs.

### Background jobs (M13)

```
caller ─▶ JobQueue.enqueue ─▶ [ pending ] ─▶ JobWorker.dequeue
                                                   │
                                    JobHandlerRegistry.get(job.name)
                                                   │
                                 validate payload ─┴─▶ handler.run ─▶ service
                                                   │
                        complete ◀── ok ── outcome ── failed ──▶ retry | dead-letter
```

Two backends behind one `JobQueue` protocol: `InMemoryJobQueue` is
deterministic, offline, and the default, and `RedisJobQueue` is durable and
shared. The same contract tests are written against both — two
implementations are only interchangeable if the same statements hold for each.

- **Validation is shared, not per backend.** A payload must survive a JSON
  round trip *unchanged*, which is stricter than `json.dumps` alone: a tuple
  serialises fine and returns as a list, so accepting one would mean the
  in-memory queue held a different value than Redis would return for the same
  job. Checking it in one place is what stops "works in tests, fails in
  production".
- **Jobs carry no timestamps.** Ordering belongs to the queue. A clock inside
  the value would make every comparison depend on when it ran.
- **Handoff is at-least-once.** `dequeue` uses Redis `LMOVE` to shift the id
  from `pending` to `processing` in one server-side operation, so a worker that
  dies mid-job leaves the id visible rather than dropping it. Reclaiming
  stranded ids needs a lease clock and an owner, and is not implemented — what
  matters here is that the id is still there to reclaim.
- **Keys are namespaced.** A Redis server is far likelier to be shared between
  projects than a database is. Tests run on database index 15 under a
  `nexaassist:test:` prefix and clean up by scanning it; nothing ever calls
  `FLUSHDB`.
- **Retryable and permanent failures are different.** An unregistered handler
  or a payload that does not match its schema will fail identically every time,
  so both dead-letter on the first attempt. Spending the remaining budget on
  them only delays the outcome and buries the cause under repeats.
- **An unexpected exception is retried**, unlike in the tool executor. The
  usual cause of one in background work is a dependency that was briefly
  unavailable, and the attempt budget already bounds how long that can go on.
- **`drain` is bounded.** A re-queued job lands back in the same queue the loop
  is reading, so an unbounded drain against a permanently failing job would
  spin until its attempts ran out with no way to regain control.
- **Handlers do not touch the session.** The service already owns the
  transaction boundary; a handler that also committed would be a second opinion
  about a boundary that has an owner.
- **No HTTP surface.** The roadmap specifies none for M13, and the OpenAPI
  schema is unchanged.

### Realtime (M14)

```
client ──ws──▶ /api/v1/ws
                  │  accept ─▶ registry (ceiling) ─▶ Ready
                  │
                  ├─ ping  ──────────────────────────▶ Pong
                  └─ ask   ─▶ AnswerStreamer ─▶ StreamingLLMProvider
                                   │
                                   └─▶ Delta … Delta ─▶ Complete   (or Error)
```

- **Every frame is a validated envelope.** A socket that accepts whatever
  arrives is an undocumented API that changes shape whenever a caller does —
  and FastAPI documents HTTP operations, so a WebSocket route never appears in
  OpenAPI. The contract lives in `app/realtime/envelope.py` and the tests are
  what pin it; one test records the OpenAPI absence as a known fact.
- **Inbound frames are a discriminated union with `extra="forbid"`**, so an
  unknown type is rejected at the edge rather than reaching a handler that
  quietly does nothing.
- **Two limits, protecting two different things.** The frame ceiling protects
  memory; the question length bounds what becomes a model call.
- **Connections are counted against one registry.** A ceiling only means
  anything if every connection is counted against the same one, which is why
  the dependency caches.
- **One stream at a time per connection.** Otherwise a client could open a
  single socket and start an unbounded number of concurrent model calls — a
  cheaper way to exhaust the process than opening connections, which at least
  are counted.
- **Failures are frames, not exceptions.** A malformed frame is answered and
  the connection kept, because a client bug on one message is not a reason to
  drop the session. A provider failure ends that answer with an `error` frame
  and no `complete`, since a completion frame would assert a whole answer
  exists. Only oversize and over-capacity close the socket, with the standard
  1009 and 1008 codes.
- **Streaming is a separate protocol from `LLMProvider`.** Not every backend
  can stream, and structured output pulls the other way: a schema-validated
  object is only valid once complete, so a stream of partial JSON would be a
  stream of things that are not yet the thing.
- **This is not the grounded path.** `POST /api/v1/documents/answer` rebuilds
  citations from retrieval, and citations can only exist once an answer is
  whole. What streams here is prose, and nothing it produces is presented as
  sourced.
- **The HTTP surface is unchanged.** OpenAPI is byte-identical across all of
  M14.

### Migrations

Alembic owns the schema, and only Alembic. `alembic/env.py` resolves the
database URL from the application's `Settings` — the same object the running
service uses — so there is one place that decides which database is addressed,
and `alembic.ini` holds no URL for a credential to hide in. A caller may
override the URL programmatically; the test suite uses that to point at
`nexaassist_test`.

`target_metadata` comes from `app.models`, which imports every model module.
That import is what registers a table on `Base.metadata`: a model that is never
imported looks to autogenerate like a table that ought to be dropped.

The environment enables `compare_type` and `compare_server_default`, which
Alembic leaves off by default. Without them a column whose type or default
changed is silently ignored, and environments drift apart in a way that only
shows up much later.

Working rules: read every autogenerated migration rather than trusting it;
every revision has a real `downgrade`, proven by an upgrade/downgrade/upgrade
test against a live database; never edit a pushed migration, correct it with a
new one. A test asserts the history has exactly one head, because a branched
chain is far cheaper to catch before a merge than after.

## Planned components

_None of these exist yet; listed so the module map above has a rationale._

- Agent orchestration layer
- Retrieval / knowledge layer
- Relational persistence
- Authentication and tenancy
- Workflow definition and execution engine

## Open questions

- Deployment target?
- Single-tenant or multi-tenant?
- Synchronous request/response only, or streaming and async jobs?
