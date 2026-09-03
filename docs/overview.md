# Overview

## What NexaAssist is

NexaAssist is an agentic customer support and workflow automation platform. It
takes an inbound customer request, works out what it is, and then either
answers it from a knowledge base it can cite, drives it through a defined
business workflow, or hands it to a human — recording enough about each step
that the decision can be explained afterwards.

The distinguishing constraint is that none of that is a black box. Every answer
carries its sources. Every run leaves a trace with its cost. Every escalation
happened because a stated rule fired. A support system that cannot say why it
said something is one nobody can be accountable for.

## Problem

Support teams answer the same questions repeatedly from knowledge that already
exists in writing, and the answers still have to be right. The two usual
failure modes pull against each other:

* **A model that answers from memory** is fluent, fast, and confidently wrong
  often enough that a human has to check everything — which removes the saving.
* **A rules engine** is predictable and cannot handle the phrasing of a real
  customer, so it deflects to a human constantly.

The gap is a system that reasons about a request but answers only from
retrieved, citable material, and that escalates on deterministic criteria
rather than on a model's opinion of its own confidence.

## Target users

* **Support agents** — who need a drafted, sourced answer they can verify in
  seconds rather than a search across four systems.
* **Support leads** — who need to see what was automated, what was escalated,
  and what it cost.
* **Operations teams** — who need routine multi-step requests executed the
  same way every time.

## Core capabilities

- [x] **Conversational intake** — multi-turn sessions with persisted history
      and a bounded context window.
- [x] **Intent routing** — classification with a confidence floor, below which
      the request goes to a general handler rather than a guessed specialist.
- [x] **Knowledge-grounded answers** — pgvector retrieval, and an answer that
      either cites its sources or declines to answer.
- [x] **Tool use** — a bounded agent loop with explicit step and call ceilings,
      so a run cannot spiral.
- [x] **Workflow execution** — defined multi-step workflows with recorded runs.
- [x] **Deterministic policy** — escalation and refusal decided by stated
      rules, not by a model's self-assessment.
- [x] **Human-in-the-loop** — review items for anything the system should not
      resolve alone.
- [x] **Realtime delivery** — answers streamed over a WebSocket authenticated
      by short-lived, single-use tickets.
- [x] **Observability** — structured traces, metrics with bounded cardinality,
      and per-call cost accounting in exact decimal.
- [x] **Evaluation** — a regression suite over the agent's behaviour, so a
      prompt change is a measurable change rather than a hopeful one.

## Non-goals

* **Not a ticketing system of record.** It models tickets to act on them, not
  to replace the system a team already runs on.
* **Not a general chatbot.** It answers from supplied documents; without them
  it declines rather than improvising.
* **Not multi-tenant SaaS.** Authorization scopes resources to a subject, which
  is not the same as tenant isolation, and the difference matters.
* **Not autonomous.** Anything a policy marks as consequential goes to a human.

## Success criteria

1. An answer either carries citations or is a refusal — never a confident
   claim with nothing behind it.
2. The service starts and reports its state with no database, no Redis and no
   provider key configured.
3. Nothing reaches a log or an error body that a customer would not want
   there: not their message, not a card number, not a key.
4. A schema changes only when somebody runs a migration.
5. The test suite passes offline, on a clean checkout, without credentials.

Each of these is asserted by tests rather than asserted here.

## Current status

M0–M23 are complete: the platform, its hardening, the client, containers and
CI. See [milestones.md](milestones.md) for what each milestone delivered and
[architecture.md](architecture.md) for the design decisions behind them.
