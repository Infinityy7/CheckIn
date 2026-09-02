"""Activities specialist agent."""

from prompts.activities import SYSTEM_PROMPT, build_user_prompt
from schemas import TripPreferences

from .base import BaseAgent


class ActivitiesAgent(BaseAgent):
    """Researches and recommends activities and experiences using web search."""

    default_category = "activity"

    @property
    def agent_name(self) -> str:
        return "Activities Agent"

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_user_prompt(self, prefs: TripPreferences, context_brief: str) -> str:
        """Build the activities-specific user prompt."""
        return build_user_prompt(prefs.model_dump_json(indent=2), context_brief)
