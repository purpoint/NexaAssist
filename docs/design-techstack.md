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

## Not yet decided

- LLM / agent framework
- Vector store and embedding model
- Relational database and migration tool
- Cache and background job runner
- Authentication provider
- UI component library and styling approach
- Hosting, CI, and observability stack

## Dependency policy

Add a dependency only when the code being written in that same change needs it.
The current dependency set is deliberately three runtime packages and two dev
packages.
