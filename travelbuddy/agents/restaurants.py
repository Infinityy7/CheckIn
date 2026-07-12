"""Restaurant specialist agent."""

from prompts.restaurants import SYSTEM_PROMPT, build_user_prompt
from schemas import TripPreferences

from .base import BaseAgent


class RestaurantAgent(BaseAgent):
    """Researches and recommends dining experiences using web search."""

    @property
    def agent_name(self) -> str:
        return "Restaurant Agent"

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_user_prompt(self, prefs: TripPreferences, context_brief: str) -> str:
        """Build the restaurant-specific user prompt."""
        return build_user_prompt(prefs.model_dump_json(indent=2), context_brief)
