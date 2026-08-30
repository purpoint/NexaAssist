# Architecture

> Placeholder — records the intended shape of the system. Nothing below the
> "Current state" section is implemented yet.

## Current state

```
Client (React + TS)  ──HTTP──▶  FastAPI service
                                  └── GET /api/health
```

That is the entire runtime today: one HTTP service exposing a health check, and
a frontend shell that does not yet call it.

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
| `app.main` | Application factory: creates the FastAPI app, applies middleware, mounts routers. |
| `app.core.config` | Settings loaded from environment, validated once at startup. |
| `app.api.routes` | HTTP endpoints. One module per resource. |
| `app.schemas` | Request/response models — the API contract. |
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
