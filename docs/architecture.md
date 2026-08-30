# Architecture

> Placeholder — records the intended shape of the system. Nothing below the
> "Current state" section is implemented yet.

## Current state

```
Client (React + TS)  ──HTTP──▶  FastAPI service
                                  ├── GET /api/v1/health
                                  └── GET /api/health   (deprecated alias)
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
- **It is removed in M2**, along with the setting and its tests.

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
| `app.api.v1` | Version 1 HTTP endpoints. One module per resource. |
| `app.schemas` | Request/response models — the API contract. `common.py` holds shapes used by more than one endpoint. |
| `app.models` | Persistence models. Empty until a database is introduced. |
| `app.services` | Domain logic and orchestration. Empty until workflows are introduced. |

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
