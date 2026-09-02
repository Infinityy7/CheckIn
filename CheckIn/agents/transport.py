"""Transport specialist agent."""

from prompts.transport import SYSTEM_PROMPT, build_user_prompt
from schemas import TripPreferences

from .base import BaseAgent
from .flight_context import briefing_expected, build_flight_briefing


class TransportAgent(BaseAgent):
    """Advises on door-to-door strategies around supplier-provided flight facts."""

    default_category = "transport"
    cache_skip_reason = "supplier flight briefing expected but unavailable; estimates stay uncached"

    def __init__(self) -> None:
        self._has_supplier_offers = False

    def cacheable_result(self) -> bool:
        return self._has_supplier_offers or not briefing_expected()

    @property
    def agent_name(self) -> str:
        return "Transport Agent"

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    async def prepare_context(self, prefs: TripPreferences, context_brief: str) -> str:
        briefing = await build_flight_briefing(prefs)
        self._has_supplier_offers = briefing is not None
        if briefing is None:
            return context_brief
        return f"{context_brief}\n\n{briefing}"

    def build_user_prompt(self, prefs: TripPreferences, context_brief: str) -> str:
        """Build the transport-specific user prompt."""
        return build_user_prompt(
            prefs.model_dump_json(indent=2),
            context_brief,
            has_supplier_offers=self._has_supplier_offers,
        )
