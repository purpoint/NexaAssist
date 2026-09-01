"""Prompt text, versioned in the repository.

Every prompt is a named constant here, paired with a version string, and
mirrored into ``docs/prompt.md``. Prompts are product surface, so they are
reviewed as code rather than buried in an f-string at a call site.

Prompts describe a task; they do not encode business policy. Escalation,
urgency, routing, and human review are decisions for later milestones, and
keeping them out of the prompt means policy can change without re-tuning the
model's behaviour.
"""

INTENT_PROMPT_VERSION = "intent/v1"
"""Bump on any change to the prompt below, so logs attribute output to text."""

INTENT_SYSTEM_PROMPT = """You classify a single customer support message into one intent category.

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
"""


GROUNDED_ANSWER_PROMPT_VERSION = "grounded-answer/v1"

GROUNDED_ANSWER_SYSTEM_PROMPT = """You answer a customer question using only the numbered sources provided.

Rules:
- Use only the sources. Do not add facts from your own knowledge.
- If the sources do not contain the answer, say so plainly and set answered to false.
- cite the numbers of every source you actually used, and no others.
- answer is a short, direct reply to the question.
- Return only the structured output the schema defines.
"""
