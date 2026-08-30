# Backend

FastAPI service for NexaAssist.

## Layout

```
app/
├── main.py          application factory; middleware and router wiring
├── core/config.py   environment-driven settings
├── api/routes/      HTTP endpoints (one module per resource)
├── schemas/         request/response models
├── models/          persistence models (empty -- no database yet)
└── services/        domain logic (empty -- no workflows yet)
```

## Run

From the repository root, with dependencies installed:

```bash
uvicorn app.main:app --reload --app-dir backend
```

- Health: `GET /api/health`
- Docs: `/docs`

## Conventions

- Routes stay thin. Anything beyond request parsing and a service call belongs
  in `services/`.
- Every endpoint declares a `response_model` from `schemas/`.
- Read configuration through `app.core.config.get_settings()` — never
  `os.environ` directly.
