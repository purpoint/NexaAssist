# Prompt Design

Prompts are product surface, not incidental strings. They live as named
constants in `backend/app/llm/prompts.py`, are reviewed like code, and are
mirrored here.

## Conventions

- One named constant per prompt; no prompt text inline at a call site.
- Every prompt carries a version string. Bump it on any change to the text, so
  a log line attributes an output to the exact prompt that produced it.
- Prompts describe a **task**, never business policy. Escalation, urgency,
  routing, prioritisation, and human review are decisions owned by later
  milestones (M8, M10, M11). Keeping them out means policy can change without
  re-tuning model behaviour.
- Changes to a prompt are accompanied by the evaluation that justified them
  (M15 builds that harness).

## System prompts

### `INTENT_SYSTEM_PROMPT` — version `intent/v1`

Classifies one customer message into a single `IntentCategory`.

- **Input:** one customer message, as the user turn.
- **Output schema:** `app.schemas.intent.IntentAnalysis` — `intent`,
  `confidence`, `reason`.
- **Model:** whatever `LLM_MODEL` names; must support structured output.

```
You classify a single customer support message into one intent category.

Categories:
- billing: payments, invoices, refunds, charges, subscriptions, pricing
- technical_support: something is broken, failing, or not working as expected
- account: sign-in, profile, permissions, account settings, cancellation
- product_question: how the product works, what it can do, availability
- complaint: dissatisfaction with the product, service, or an interaction
- other: the message fits none of the categories above

Rules:
- Choose exactly one category.
- Use "other" whenever no category clearly fits. Do not force a fit.
- confidence is your own confidence in the classification, from 0.0 to 1.0.
- reason is one short sentence, at most 280 characters, explaining the choice.
- Return only the structured output the schema defines.
```

#### On `confidence`

`confidence` is the model's **self-reported** confidence. It is not a
calibrated probability: `0.94` means the model asserted high confidence, not
that the classification is correct 94% of the time. Nothing downstream should
threshold on it as though it were measured. Structured output guarantees the
field's *shape*; it guarantees nothing about its calibration.

#### On `other`

`other` is a required escape hatch. Without it the model is forced to
miscategorise, and a confident wrong `billing` is worse for downstream routing
than an honest `other`.

## Tool / function definitions

_None yet — the tool system is M6._

## Evaluation

_None yet — the evaluation framework is M15. Until it exists, prompt changes
are reviewed by reading, and the version string records which text ran._
