"""Persistence models, and the metadata Alembic compares against.

Every model module must be imported here. ``Base.metadata`` is only complete
once each mapped class has been imported, and autogenerate compares the live
database against that metadata -- a model that is never imported looks to
Alembic like a table that should be dropped.

M4 introduces the first business tables: customers and the tickets they
raise. Conversations, messages, and anything agent-facing belong to later
milestones.
"""

from app.db.base import Base
from app.models.customer import Customer
from app.models.ticket import Ticket, TicketStatus

metadata = Base.metadata

__all__ = ["Base", "Customer", "Ticket", "TicketStatus", "metadata"]
