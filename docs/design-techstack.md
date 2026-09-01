# Design & Tech Stack

> Placeholder — captures choices made so far and the ones still open.

## Decided

| Layer | Choice | Rationale |
| --- | --- | --- |
| Backend language | Python 3.11+ | Strongest ecosystem for agent/LLM tooling. |
| Backend framework | FastAPI | Async-native, typed request/response via Pydantic, generated OpenAPI docs. |
| ASGI server | Uvicorn | Standard FastAPI pairing; fine for both dev and production behind a proxy. |
| Config | pydantic-settings | Env-driven settings validated at startup rather than read ad hoc. |
| Frontend language | TypeScript | Type safety across the API boundary. |
| Frontend framework | React | Component model and ecosystem depth. |
| Frontend build | Vite | Fast dev server, first-class TS support, minimal config. |
| Backend tests | pytest + httpx | Standard; httpx drives the ASGI app in-process without a live server. |
| LLM provider | Groq | Single provider to start; access is behind a protocol so a second one is an adapter, not a refactor. |
| LLM SDK | `groq` (official) | Typed exceptions and transport-level retry/timeout. It has no `parse()` helper, so structured output is assembled in the provider. |
| Database | PostgreSQL | Relational core with strong constraints; pgvector later rides on the same instance (M5). |
| DB toolkit | SQLAlchemy 2.0 (async) + asyncpg | The app is async end to end; a sync driver would block the event loop. |
| Migrations | Alembic | The standard companion to SQLAlchemy; migrations reviewed as code. |
| Default model | `openai/gpt-oss-120b` | One of the Groq models supporting strict structured output. Configurable via `LLM_MODEL`; nothing in the code hardcodes it. |

## Not yet decided

- Agent / orchestration framework
- Vector store and embedding model
- Cache and background job runner
- Authentication provider
- UI component library and styling approach
- Hosting, CI, and observability stack

## Dependency policy

Add a dependency only when the code being written in that same change needs it.
The current dependency set is deliberately three runtime packages and two dev
packages.
