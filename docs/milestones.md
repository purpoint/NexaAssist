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

## M1 — Core API shape

- [ ] Request/response schema conventions
- [ ] Error handling and structured logging
- [ ] First domain resource end to end

## M2 — Frontend shell

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
