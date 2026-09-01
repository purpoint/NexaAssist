"""Fitting conversation history into a context window.

History grows without bound; a context window does not. This decides what to
keep when the two disagree.

Two rules shape the result:

* **Recency wins.** The most recent turns carry the current request; the
  opening of a long exchange rarely does.
* **The window is never exceeded, even by one message.** A single turn larger
  than the whole budget is truncated rather than dropped, because dropping the
  customer's actual question and answering the rest is worse than answering a
  shortened version of it.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.models import ConversationMessage, MessageRole

logger = get_logger(__name__)

DEFAULT_MAX_TOKENS = 2_000
TRUNCATION_MARKER = " …[truncated]"


class ContextMessage(BaseModel):
    """One turn as it will be presented to a model."""

    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str = Field(min_length=1)
    tokens: int = Field(ge=0)


class ConversationContext(BaseModel):
    """The window that was assembled, and what it cost."""

    model_config = ConfigDict(frozen=True)

    messages: list[ContextMessage] = Field(default_factory=list)
    total_tokens: int = 0
    dropped: int = Field(
        default=0, description="Older turns left out because they did not fit."
    )
    truncated: bool = Field(
        default=False, description="True when a kept turn had to be shortened."
    )

    def render(self) -> str:
        """The window as prompt text."""
        return "\n".join(f"{m.role.value}: {m.content}" for m in self.messages)


class ContextWindow:
    """Selects the most recent history that fits a token budget."""

    def __init__(self, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self._max_tokens = max_tokens

    def build(self, history: Sequence[ConversationMessage]) -> ConversationContext:
        """Assemble a window from ``history``, newest turns first to survive."""
        kept: list[ContextMessage] = []
        used = 0
        truncated = False

        # Walk backwards: the newest turn is the one that must survive.
        for message in reversed(history):
            remaining = self._max_tokens - used
            if remaining <= 0:
                break

            if message.token_estimate <= remaining:
                kept.append(
                    ContextMessage(
                        role=message.role,
                        content=message.content,
                        tokens=message.token_estimate,
                    )
                )
                used += message.token_estimate
                continue

            # It does not fit. Only the newest turn is worth truncating -- an
            # older one is better dropped whole than reduced to a fragment.
            if kept:
                break
            shortened = _truncate(message.content, remaining)
            kept.append(
                ContextMessage(role=message.role, content=shortened, tokens=remaining)
            )
            used += remaining
            truncated = True
            break

        kept.reverse()
        dropped = len(history) - len(kept)

        logger.info(
            "context window built kept=%d dropped=%d tokens=%d budget=%d truncated=%s",
            len(kept),
            dropped,
            used,
            self._max_tokens,
            truncated,
        )
        return ConversationContext(
            messages=kept, total_tokens=used, dropped=dropped, truncated=truncated
        )


def _truncate(content: str, budget_tokens: int) -> str:
    """Shorten ``content`` to roughly ``budget_tokens``, marking the cut."""
    from app.services.conversation import CHARS_PER_TOKEN

    characters = max(1, budget_tokens * CHARS_PER_TOKEN - len(TRUNCATION_MARKER))
    if len(content) <= characters:
        return content
    return content[:characters].rstrip() + TRUNCATION_MARKER
