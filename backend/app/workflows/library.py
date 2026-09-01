"""The workflows this system knows how to run.

Written as data, and gathered in one place: a workflow is a product decision
about how a class of request should be handled, so it should be readable
without tracing code.

Each targets a case where the steps are already known. Where they are not, the
M7 agent exists precisely so a model can decide -- these are not a replacement
for it.
"""

from app.workflows.definition import Workflow, WorkflowStep, reference_to
from app.workflows.errors import WorkflowNotFoundError

TICKET_CONTEXT = Workflow(
    name="ticket_context",
    description=(
        "Gather everything known about one ticket: the ticket itself, plus "
        "documentation relevant to what the customer wrote."
    ),
    inputs=["ticket_id"],
    steps=[
        WorkflowStep(
            id="ticket",
            tool="lookup_ticket",
            params={"ticket_id": reference_to("ticket_id")},
        ),
        WorkflowStep(
            id="recent",
            tool="list_tickets",
            params={"limit": 5},
            # Useful context, but the run is still worth something without it.
            optional=True,
        ),
    ],
)

KNOWLEDGE_LOOKUP = Workflow(
    name="knowledge_lookup",
    description="Find documentation passages relevant to a customer question.",
    inputs=["question"],
    steps=[
        WorkflowStep(
            id="passages",
            tool="search_knowledge_base",
            params={"query": reference_to("question"), "top_k": 4},
        ),
    ],
)

OPEN_TICKET_REVIEW = Workflow(
    name="open_ticket_review",
    description="List the tickets currently awaiting attention.",
    steps=[
        WorkflowStep(id="open", tool="list_tickets", params={"status": "open", "limit": 20}),
    ],
)

WORKFLOWS: tuple[Workflow, ...] = (
    TICKET_CONTEXT,
    KNOWLEDGE_LOOKUP,
    OPEN_TICKET_REVIEW,
)


def names() -> list[str]:
    """Available workflow names, sorted so listings are stable."""
    return sorted(w.name for w in WORKFLOWS)


def get(name: str) -> Workflow:
    """Look up a workflow by name."""
    for workflow in WORKFLOWS:
        if workflow.name == name:
            return workflow
    raise WorkflowNotFoundError(details={"workflow": name})


def describe_all() -> list[dict[str, object]]:
    """What each workflow is for and what it needs, in a stable order."""
    return [
        {
            "name": w.name,
            "description": w.description,
            "inputs": list(w.inputs),
            "steps": w.step_ids(),
            "tools": w.tools_used(),
        }
        for w in sorted(WORKFLOWS, key=lambda w: w.name)
    ]
