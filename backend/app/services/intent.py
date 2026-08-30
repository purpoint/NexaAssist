"""Intent analysis application logic.

Depends on the :class:`~app.llm.base.LLMProvider` protocol and nothing else --
no vendor SDK, no FastAPI. The provider arrives through the constructor, so a
future orchestration layer can call this service directly, and a test can pass
a double without touching HTTP.
"""

from app.core.logging import get_logger
from app.llm.base import LLMPrompt, LLMProvider
from app.llm.prompts import INTENT_PROMPT_VERSION, INTENT_SYSTEM_PROMPT
from app.schemas.intent import IntentAnalysis

logger = get_logger(__name__)


class IntentService:
    """Classifies a customer message into a single :class:`IntentCategory`."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def analyze(self, message: str) -> IntentAnalysis:
        """Return the model's classification of ``message``.

        Provider failures propagate untouched: distinguishing and recovering
        from them is the error-handling step of M2, and swallowing them here
        would turn an outage into a plausible-looking answer.
        """
        completion = await self._provider.complete_structured(
            prompt=LLMPrompt(system=INTENT_SYSTEM_PROMPT, user=message),
            schema=IntentAnalysis,
        )
        analysis = completion.output

        # Metadata only. The message and the model's reason can carry customer
        # content, so neither is logged.
        logger.info(
            "intent analysed provider=%s model=%s prompt_version=%s intent=%s "
            "latency_ms=%.1f tokens_in=%d tokens_out=%d",
            completion.provider,
            completion.model,
            INTENT_PROMPT_VERSION,
            analysis.intent.value,
            completion.latency_ms,
            completion.usage.input_tokens,
            completion.usage.output_tokens,
        )
        return analysis
