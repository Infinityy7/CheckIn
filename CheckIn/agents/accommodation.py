"""Accommodation specialist agent."""

from prompts.accommodation import SYSTEM_PROMPT, build_user_prompt
from schemas import TripPreferences

from .base import BaseAgent


class AccommodationAgent(BaseAgent):
    """Researches and recommends accommodation options using web search."""

    default_category = "hotel"

    @property
    def agent_name(self) -> str:
        return "Accommodation Agent"

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_user_prompt(self, prefs: TripPreferences, context_brief: str) -> str:
        """Build the accommodation-specific user prompt."""
        return build_user_prompt(prefs.model_dump_json(indent=2), context_brief)
