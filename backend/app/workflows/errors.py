"""Workflow failures, expressed as application errors."""

from app.core.exceptions import NotFoundError


class WorkflowNotFoundError(NotFoundError):
    """No workflow is defined under the requested name."""

    code = "workflow_not_found"
    message = "The requested workflow is not defined."
