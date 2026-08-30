# Architecture

> Placeholder — records the intended shape of the system. Nothing below the
> "Current state" section is implemented yet.

## Current state

```
Client (React + TS)  ──HTTP──▶  FastAPI service
                                  ├── GET  /api/v1/health
                                  ├── POST /api/v1/intent/analyze
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

### Error handling (current state)

Every provider failure currently surfaces as a single `LLMError` (500). That is
deliberate for this checkpoint and honest about how little the layer
distinguishes so far. Classifying timeouts, rate limits, provider outages, and
misconfiguration — each with its own status code — is the remaining M2 step.

## Intent analysis

The first capability that actually calls a model. The chain is one-way, and
each link depends only on the one below it:

```
POST /api/v1/intent/analyze
  └─ api/v1/intent.py      validate request, call service, return  (no logic)
      └─ IntentService     compose prompt + schema, unwrap result
          └─ LLMProvider   protocol -- the service knows nothing else
              └─ GroqProvider     the only module importing the SDK
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

## Planned components

_None of these exist yet; listed so the module map above has a rationale._

- Agent orchestration layer
- Retrieval / knowledge layer
- Relational persistence
- Caching and background jobs
- Authentication and tenancy
- Workflow definition and execution engine

## Open questions

- Deployment target?
- Single-tenant or multi-tenant?
- Synchronous request/response only, or streaming and async jobs?
