"""Running a workflow.

The deterministic counterpart to the M7 agent: the steps are already decided,
so nothing is asked of a model. That is the point -- when the right sequence is
known, letting a model rediscover it each time adds cost and variance without
adding judgement.

Execution reuses the M6 executor, so every step inherits the guarantees already
established there: parameters validated, calls bounded in time, and failures
returned rather than raised.
"""

import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.tools.execution import ToolExecutor
from app.tools.results import ToolOutcome, ToolResult
from app.workflows.definition import Workflow, WorkflowStep, referenced_step

logger = get_logger(__name__)


class StepRecord(BaseModel):
    """What one step did."""

    model_config = ConfigDict(frozen=True)

    id: str
    tool: str
    outcome: ToolOutcome
    skipped: bool = False
    error: str | None = None
    duration_ms: float = 0.0


class WorkflowRun(BaseModel):
    """The outcome of a whole workflow.

    Carries a record of every step but not their outputs: a run summary may be
    logged or returned, and outputs carry customer content. Callers that need
    the data read ``outputs`` from the runner directly.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    workflow: str
    completed: bool
    steps: list[StepRecord] = Field(default_factory=list)
    failed_step: str | None = None
    duration_ms: float = 0.0


class WorkflowRunner:
    """Executes a :class:`Workflow` step by step."""

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    async def run(
        self, workflow: Workflow, *, inputs: dict[str, Any] | None = None
    ) -> tuple[WorkflowRun, dict[str, Any]]:
        """Run ``workflow``, returning its summary and each step's output.

        Outputs are returned separately from the summary so that the thing
        which is safe to log and the thing which holds customer data are not
        the same object.
        """
        run_id = uuid.uuid4()
        started = time.perf_counter()
        outputs: dict[str, Any] = dict(inputs or {})
        records: list[StepRecord] = []
        failed_step: str | None = None

        for step in workflow.steps:
            result = await self._executor.execute(
                step.tool, self._resolve(step, outputs)
            )
            records.append(
                StepRecord(
                    id=step.id,
                    tool=step.tool,
                    outcome=result.outcome,
                    error=result.error,
                    duration_ms=result.duration_ms,
                )
            )

            if result.ok:
                outputs[step.id] = result.output
                continue

            if step.optional:
                # Declared as skippable, so the run continues without its
                # output. A later step referencing it will fail validation
                # rather than silently receiving nothing.
                logger.info(
                    "workflow step failed but is optional run_id=%s step=%s outcome=%s",
                    run_id,
                    step.id,
                    result.outcome.value,
                )
                continue

            failed_step = step.id
            break

        return (
            self._finish(run_id, workflow, records, failed_step, started),
            outputs,
        )

    def _resolve(self, step: WorkflowStep, outputs: dict[str, Any]) -> dict[str, Any]:
        """Substitute references to earlier outputs.

        A reference to a step that did not produce output resolves to ``None``
        rather than raising: the executor then rejects it as invalid
        parameters, which is a recorded outcome instead of a crash.
        """
        resolved: dict[str, Any] = {}
        for key, value in step.params.items():
            source = referenced_step(value)
            resolved[key] = outputs.get(source) if source is not None else value
        return resolved

    def _finish(
        self,
        run_id: uuid.UUID,
        workflow: Workflow,
        records: list[StepRecord],
        failed_step: str | None,
        started: float,
    ) -> WorkflowRun:
        duration = (time.perf_counter() - started) * 1000.0
        completed = failed_step is None
        logger.info(
            "workflow finished run_id=%s workflow=%s steps=%d completed=%s "
            "failed_step=%s duration_ms=%.1f",
            run_id,
            workflow.name,
            len(records),
            completed,
            failed_step or "-",
            duration,
        )
        return WorkflowRun(
            run_id=str(run_id),
            workflow=workflow.name,
            completed=completed,
            steps=records,
            failed_step=failed_step,
            duration_ms=duration,
        )


def unresolved_references(workflow: Workflow, available: set[str]) -> set[str]:
    """Declared inputs the caller has not supplied.

    Lets a caller check a workflow against the inputs it intends to pass before
    running anything, rather than discovering the gap as a failed step.
    """
    produced = set(available)
    missing: set[str] = set()
    for step in workflow.steps:
        missing |= step.references() - produced
        produced.add(step.id)
    return missing


__all__ = [
    "StepRecord",
    "ToolResult",
    "WorkflowRun",
    "WorkflowRunner",
    "unresolved_references",
]
