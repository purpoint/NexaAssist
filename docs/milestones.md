# Milestones

Each milestone is a self-contained change set: code, docs, and tests together.
Scope is intentionally narrow so nothing is built speculatively.

## M0 — Repository foundation ✅

- Directory structure for backend, frontend, docs, and tests
- `.gitignore`, `.env.example`, root `README.md`
- FastAPI application factory with a single `/api/health` endpoint
- Environment-driven settings
- React + TypeScript project scaffolding (structure and config only, no UI)
- Backend test covering the health endpoint

## M1 — Versioned API foundation ✅

- [x] API versioning: v1 router tree mounted at `/api/v1`
- [x] Health endpoint moved to `app/api/v1/health.py`
- [x] `/api/health` preserved as a deprecated, config-gated alias
- [x] `AppError` foundation and consistent `ErrorResponse` error bodies
- [x] Standard-library logging configuration
- [x] Tests for v1 health, the legacy alias, app creation, and 404 handling
- [ ] First domain resource end to end _(deferred to M2 — needs persistence)_

## M2 — Frontend shell

- [ ] Remove the deprecated `/api/health` alias and `ENABLE_LEGACY_HEALTH_ROUTE`
- [ ] Install frontend dependencies
- [ ] Application shell and routing
- [ ] Typed API client wired to the backend

## M3 — Persistence

- [ ] Database selection and connection layer
- [ ] Migrations
- [ ] Persistence models

## M4 — Agent layer

- [ ] Model provider integration
- [ ] Agent loop and tool interface
- [ ] Prompts documented in `prompt.md`

## M5 — Knowledge / retrieval

- [ ] Document ingestion
- [ ] Vector store and retrieval
- [ ] Grounded answers with citations

## M6 — Workflow automation

- [ ] Workflow definition format
- [ ] Execution engine
- [ ] Human escalation and handoff

## M7 — Authentication & multi-tenancy

- [ ] Authentication
- [ ] Authorization and tenant isolation

## M8 — Production readiness

- [ ] CI pipeline
- [ ] Observability
- [ ] Deployment
