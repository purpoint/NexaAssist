"""Persistence models, and the metadata Alembic compares against.

Every model module must be imported here. ``Base.metadata`` is only complete
once each mapped class has been imported, and autogenerate compares the live
database against that metadata -- a model that is never imported looks to
Alembic like a table that should be dropped.

M3 ships no business tables. Tickets, conversations, and customers arrive with
M4; this milestone provides the foundation they will be built on.
"""

from app.db.base import Base

# Import model modules here as they are added, e.g.:
#     from app.models.ticket import Ticket  # noqa: F401
# The import is what registers the table on Base.metadata.

metadata = Base.metadata

__all__ = ["Base", "metadata"]
