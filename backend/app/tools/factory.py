"""Where the available tools are decided.

One readable list rather than import-time discovery: what the system can be
asked to do should be inspectable in a single place. Registration happens per
request because these tools hold request-scoped services.
"""

from app.services.document import DocumentService
from app.services.ticket import TicketService
from app.tools.domain import (
    ListTicketsTool,
    LookupTicketTool,
    SearchKnowledgeBaseTool,
)
from app.tools.registry import ToolRegistry


def build_registry(
    *, tickets: TicketService, documents: DocumentService
) -> ToolRegistry:
    """Assemble the registry for one request."""
    registry = ToolRegistry()
    registry.register(LookupTicketTool(tickets))
    registry.register(ListTicketsTool(tickets))
    registry.register(SearchKnowledgeBaseTool(documents))
    return registry
