"""Response shapes shared across endpoints.

Kept deliberately small. Only add a model here when more than one endpoint
genuinely needs it -- resource-specific schemas belong in their own module.
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """The body returned for every error the API raises.

    A single error shape means clients parse one thing, and it is what
    ``app.core.exceptions`` renders for both application errors and HTTP
    errors such as an unmatched route.
    """

    code: str = Field(description="Stable, machine-readable error identifier.")
    message: str = Field(description="Human-readable explanation of the error.")
    details: Any | None = Field(
        default=None,
        description="Optional structured context about the error.",
    )
