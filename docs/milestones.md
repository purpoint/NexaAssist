# Milestones

Each milestone is a self-contained change set: code, docs, and tests together.
Scope is intentionally narrow so nothing is built speculatively.

## M0 — Repository Foundation ✅

- Directory structure for backend, frontend, docs, and tests
- `.gitignore`, `.env.example`, root `README.md`
- FastAPI application factory with a single `/api/health` endpoint
- Environment-driven settings
- React + TypeScript project scaffolding (structure and config only, no UI)
- Backend test covering the health endpoint

## M1 — FastAPI Foundation ✅

- [x] API versioning: v1 router tree mounted at `/api/v1`
- [x] Health endpoint moved to `app/api/v1/health.py`
- [x] `/api/health` preserved as a deprecated, config-gated alias
- [x] `AppError` foundation and consistent `ErrorResponse` error bodies
- [x] Standard-library logging configuration
- [x] Tests for v1 health, the legacy alias, app creation, and 404 handling

## M2 — LLM Foundation

Delivered in three checkpoints.

- [x] LLM configuration on `Settings`, and the provider abstraction
      (`LLMProvider` protocol, `LLMConfig`, `LLMPrompt`, `StructuredCompletion`)
- [x] Groq provider foundation and a deterministic `StaticLLMProvider`
- [x] Structured intent analysis: `IntentAnalysis`, `IntentService`, and
      `POST /api/v1/intent/analyze`
- [ ] Hardened LLM error taxonomy, bounded repair retry, outer request
      deadline, and secret redaction in logging

## M3 — PostgreSQL + Alembic

- [ ] Database connection layer
- [ ] Alembic migrations
- [ ] Persistence models

## M4 — Core Business Domain

- [ ] First domain resource end to end _(carried over from M1, needed persistence)_
- [ ] Domain models and service layer

## M5 — RAG + pgvector

- [ ] Document ingestion
- [ ] pgvector storage and retrieval
- [ ] Grounded answers with citations

## M6 — Tool System

- [ ] Tool interface and registry
- [ ] Tool execution and result handling

## M7 — Agent Core

- [ ] Agent loop over the tool system
- [ ] Agent state and step accounting

## M8 — Intent Router

- [ ] Route classified intents to handlers
- [ ] Fallback and ambiguity handling

## M9 — Specialized Workflows

- [ ] Workflow definition format
- [ ] Workflow execution

## M10 — Deterministic Policy Engine

- [ ] Policy rules evaluated outside the model
- [ ] Policy precedence over model output

## M11 — Human-in-the-Loop

- [ ] Escalation criteria
- [ ] Handoff and human review queue

## M12 — Conversation Memory

- [ ] Conversation state and history
- [ ] Context window management

## M13 — Redis + Background Jobs

- [ ] Redis integration
- [ ] Background job execution

## M14 — WebSockets + Realtime

- [ ] WebSocket transport
- [ ] Streaming responses

## M15 — Evaluation Framework

- [ ] Prompt and workflow evaluation harness
- [ ] Regression suite for model-facing changes

## M16 — Observability + Cost Tracking

- [ ] Structured tracing
- [ ] Token and cost accounting

## M17 — Security Hardening

- [ ] Authentication
- [ ] Authorization and tenant isolation

## M18 — Production Frontend

- [ ] Remove the deprecated `/api/health` alias and `ENABLE_LEGACY_HEALTH_ROUTE`
      _(originally scheduled for M2; retargeted here, the first milestone with a
      real API consumer)_
- [ ] Install frontend dependencies
- [ ] Application shell and routing
- [ ] Typed API client wired to the backend

## M19 — Docker / Production-like Environment

- [ ] Container images
- [ ] Local production-like compose environment

## M20 — CI/CD + Deployment

- [ ] CI pipeline
- [ ] Deployment

## M21 — Documentation

- [ ] Architecture and operations documentation
- [ ] API reference

## M22 — Resume + Interview Preparation

- [ ] Project write-up
- [ ] Architecture walkthrough
