"""The policy rules this system actually enforces.

Gathered in one readable block, in precedence order, because "what will this
system refuse to say" is a question that should be answerable by reading a
single file rather than tracing code.

Every rule here is a pure function of the context. None consults a model, a
database, or the clock.
"""

import re

from app.policy.rules import PolicyAction, PolicyContext, PolicyOutcome
from app.schemas.intent import IntentCategory

REVIEW_REPLY = (
    "I have passed this to a support agent, who will confirm the details with you."
)

COMMITMENT_PATTERNS = (
    re.compile(r"(?i)\b(i|we)\s+(have\s+)?(refunded|reimbursed|credited)\b"),
    # \s* not \s+ before the contraction: "I'll" has no space, and a
    # promise that slips through on punctuation is still a promise.
    re.compile(r"(?i)\b(i|we)\s*(will|'ll|’ll)\s+(refund|reimburse|credit|cancel)\b"),
    re.compile(r"(?i)\bguarantee(d)?\b"),
    re.compile(r"(?i)\bfull\s+refund\b"),
)
"""Language that commits the business to money or an outcome.

Deliberately conservative and pattern-based rather than model-judged: a rule
that asks a model whether a reply is safe inherits that model's variance, which
is the opposite of what a policy engine is for.
"""


class NoFinancialCommitments:
    """A reply may not promise refunds, credits, or guarantees.

    The system has no authority to move money, so a confident sentence saying
    otherwise is a liability regardless of how the model arrived at it.
    """

    name = "no_financial_commitments"
    description = "Replies must not commit to refunds, credits, or guarantees."

    def evaluate(self, context: PolicyContext) -> PolicyOutcome | None:
        if any(p.search(context.proposed_reply) for p in COMMITMENT_PATTERNS):
            return PolicyOutcome(
                action=PolicyAction.REPLACE,
                rule=self.name,
                reason="The reply committed to a financial outcome.",
                replacement=REVIEW_REPLY,
            )
        return None


class ComplaintsGoToAHuman:
    """Complaints are acknowledged, not argued with.

    An automated rebuttal to a complaint is the single most reliable way to
    turn one into an escalation, so the category is answered by a person.
    """

    name = "complaints_go_to_a_human"
    description = "Complaints are handed to a support agent rather than answered."

    def evaluate(self, context: PolicyContext) -> PolicyOutcome | None:
        if context.analysis.intent is IntentCategory.COMPLAINT:
            return PolicyOutcome(
                action=PolicyAction.REPLACE,
                rule=self.name,
                reason="Complaints are answered by a person.",
                replacement=REVIEW_REPLY,
            )
        return None


class UnresolvedRequestsAreNotClaimedAsAnswered:
    """A handler that failed must not sound like it succeeded.

    ``handled=False`` with a confident-sounding reply is worse than an honest
    hand-off, because it stops anyone looking at it again.
    """

    name = "unresolved_requests_reach_a_human"
    description = "An unresolved request is handed on rather than answered."

    def evaluate(self, context: PolicyContext) -> PolicyOutcome | None:
        if not context.handled:
            return PolicyOutcome(
                action=PolicyAction.REPLACE,
                rule=self.name,
                reason="The handler could not resolve the request.",
                replacement=REVIEW_REPLY,
            )
        return None


DEFAULT_RULES = (
    # Money first: it is the costliest thing to get wrong.
    NoFinancialCommitments(),
    ComplaintsGoToAHuman(),
    UnresolvedRequestsAreNotClaimedAsAnswered(),
)


def default_rules() -> list[object]:
    """The standard rule set, in precedence order."""
    return list(DEFAULT_RULES)
