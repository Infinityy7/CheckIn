"""Transport specialist agent."""

from prompts.transport import SYSTEM_PROMPT, build_user_prompt
from schemas import TripPreferences

from .base import BaseAgent


class TransportAgent(BaseAgent):
    """Researches and recommends transport strategies using web search."""

    @property
    def agent_name(self) -> str:
        return "Transport Agent"

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_user_prompt(self, prefs: TripPreferences, context_brief: str) -> str:
        """Build the transport-specific user prompt."""
        return build_user_prompt(prefs.model_dump_json(indent=2), context_brief)
