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
├── db/
│   ├── base.py         DeclarativeBase, naming convention, TimestampMixin
│   ├── engine.py       async engine + pool lifecycle
│   ├── session.py      request-scoped AsyncSession dependency
│   └── errors.py       database failures as AppError subclasses
├── llm/
│   ├── base.py         vendor-neutral contract: LLMProvider, LLMConfig, ...
│   ├── errors.py       LLM failures as AppError subclasses
│   ├── factory.py      provider registry and the get_llm_provider dependency
│   ├── prompts.py      prompt text, versioned as named constants
│   └── providers/      concrete implementations (groq, static)
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

### Database

```bash
createdb nexaassist
```

Then set `DATABASE_URL` in `.env`. The async driver is required —
`postgresql://` is rejected at startup. Leaving it unset runs the service
without a database.

The schema is owned by migrations; nothing calls `create_all()` and nothing
migrates automatically at startup.

### Without provider credentials

Set `LLM_PROVIDER=static` to use the deterministic offline provider. It
makes no network calls and needs no API key. Otherwise export `GROQ_API_KEY`
(or put it in `.env`, which is git-ignored).

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
- Reach the language model through `Depends(get_llm_provider)` and the
  `LLMProvider` protocol — never by importing a provider module or the vendor
  SDK outside `llm/providers/`.
- `app.core` must not import `app.llm`; the dependency runs one way.
- Never log prompts, model responses, or credentials. Log metadata only:
  provider, model, latency, token usage, stop reason.
- Reach the database through `Depends(get_db_session)`. Sessions do not commit
  implicitly — a caller that writes commits explicitly.
- Never call `Base.metadata.create_all()`. The schema belongs to migrations.
