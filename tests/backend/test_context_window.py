"""Context window management. Offline."""

import logging

import pytest

from app.models import MessageRole
from app.services.context import (
    TRUNCATION_MARKER,
    ContextWindow,
    ConversationContext,
)


class Msg:
    """Stands in for a ConversationMessage without a database."""

    def __init__(self, position: int, content: str, tokens: int, role: MessageRole = MessageRole.CUSTOMER):
        self.position, self.content, self.token_estimate, self.role = (
            position,
            content,
            tokens,
            role,
        )


def history(*sizes: int) -> list[Msg]:
    return [Msg(i, f"turn {i} " + "x" * (s * 4), s) for i, s in enumerate(sizes)]


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------


def test_everything_is_kept_when_it_fits() -> None:
    context = ContextWindow(max_tokens=100).build(history(10, 10, 10))

    assert len(context.messages) == 3
    assert context.total_tokens == 30
    assert context.dropped == 0
    assert context.truncated is False


def test_an_empty_history_yields_an_empty_window() -> None:
    context = ContextWindow().build([])

    assert context.messages == []
    assert context.total_tokens == 0
    assert context.render() == ""


def test_the_budget_is_never_exceeded() -> None:
    for budget in (1, 7, 25, 100):
        context = ContextWindow(max_tokens=budget).build(history(10, 10, 10, 10))
        assert context.total_tokens <= budget, budget


# --------------------------------------------------------------------------
# Recency
# --------------------------------------------------------------------------


def test_the_newest_turns_survive() -> None:
    """The current request lives at the end of the exchange."""
    context = ContextWindow(max_tokens=20).build(history(10, 10, 10))

    assert [m.content[:6] for m in context.messages] == ["turn 1", "turn 2"]
    assert context.dropped == 1


def test_kept_messages_stay_in_reading_order() -> None:
    context = ContextWindow(max_tokens=30).build(history(10, 10, 10))

    assert [m.content[:6] for m in context.messages] == ["turn 0", "turn 1", "turn 2"]


def test_dropped_counts_what_was_left_out() -> None:
    context = ContextWindow(max_tokens=10).build(history(10, 10, 10, 10))

    assert len(context.messages) == 1
    assert context.dropped == 3


# --------------------------------------------------------------------------
# Truncation
# --------------------------------------------------------------------------


def test_a_single_oversized_turn_is_truncated_not_dropped() -> None:
    """Dropping the customer's actual question is worse than shortening it."""
    context = ContextWindow(max_tokens=5).build(history(50))

    assert len(context.messages) == 1
    assert context.truncated is True
    assert context.messages[0].content.endswith(TRUNCATION_MARKER)
    assert context.total_tokens <= 5


def test_an_older_oversized_turn_is_dropped_whole() -> None:
    """A fragment of old context is worth less than the space it costs."""
    context = ContextWindow(max_tokens=12).build(history(10, 50))
    # The newest (50) does not fit; it is truncated as the only kept message.
    assert context.truncated is True
    assert len(context.messages) == 1


def test_truncation_only_applies_to_the_newest_turn() -> None:
    context = ContextWindow(max_tokens=15).build(history(50, 10))

    # turn 1 (10) fits; turn 0 (50) does not and is dropped rather than cut.
    assert [m.content[:6] for m in context.messages] == ["turn 1"]
    assert context.truncated is False
    assert context.dropped == 1


# --------------------------------------------------------------------------
# Rendering and shape
# --------------------------------------------------------------------------


def test_render_labels_each_turn_with_its_role() -> None:
    context = ContextWindow(max_tokens=100).build(
        [Msg(0, "hello", 2, MessageRole.CUSTOMER), Msg(1, "hi", 1, MessageRole.ASSISTANT)]
    )

    assert context.render() == "customer: hello\nassistant: hi"


def test_the_result_is_immutable() -> None:
    context = ContextWindow().build(history(1))

    with pytest.raises(Exception):
        context.total_tokens = 999


def test_a_nonsensical_budget_is_rejected() -> None:
    with pytest.raises(ValueError):
        ContextWindow(max_tokens=0)


def test_building_is_deterministic() -> None:
    window, messages = ContextWindow(max_tokens=25), history(10, 10, 10)

    assert window.build(messages) == window.build(messages)


def test_the_context_module_consults_no_model() -> None:
    from pathlib import Path

    source = Path("backend/app/services/context.py").read_text()
    for forbidden in ("app.llm", "groq", "fastapi"):
        assert forbidden not in source


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


def test_logs_record_counts_not_content(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.context"):
        ContextWindow(max_tokens=10).build(
            [Msg(0, "card 4242 was charged twice", 20)]
        )

    assert "kept=1" in caplog.text and "budget=10" in caplog.text
    assert "4242" not in caplog.text


def test_context_is_serialisable() -> None:
    import json

    json.dumps(ContextWindow().build(history(1, 2)).model_dump(mode="json"))


def test_empty_context_type() -> None:
    assert isinstance(ContextWindow().build([]), ConversationContext)
