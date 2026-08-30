# Backend

FastAPI service for NexaAssist.

## Layout

```
app/
├── main.py             application factory; middleware, error handlers, router wiring
├── core/
│   ├── config.py       environment-driven settings
│   ├── exceptions.py   AppError base class and the exception handlers
│   └── logging.py      stdlib logging configuration
├── api/v1/
│   ├── router.py       aggregates v1 routes; mounted once by main.py
│   └── health.py       GET /health
├── schemas/            request/response models (common.py holds shared shapes)
├── models/             persistence models (empty -- no database yet)
└── services/           domain logic (empty -- no workflows yet)
```

## Run

From the repository root, with dependencies installed:

```bash
uvicorn app.main:app --reload --app-dir backend
```

- Health: `GET /api/v1/health`
- Docs: `/docs`

## Conventions

- Routes stay thin. Anything beyond request parsing and a service call belongs
  in `services/`.
- Every endpoint declares a `response_model` from `schemas/`.
- Read configuration through `Depends(get_settings)` in endpoints, or
  `app.core.config.get_settings()` elsewhere — never `os.environ` directly.
- New v1 routes get a module in `api/v1/` and one `include_router` line in
  `api/v1/router.py`. Never hardcode the version segment in a path; it comes
  from `Settings.api_v1_prefix`.
- Raise `AppError` subclasses from `core/exceptions.py` for expected failures;
  the registered handlers render them as `ErrorResponse`.
