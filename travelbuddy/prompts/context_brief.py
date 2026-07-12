"""Prompt for generating the shared trip context brief."""


def build_context_brief_prompt(prefs_json: str) -> str:
    """Return the user message that asks Claude to produce a trip context brief."""
    return f"""You are a senior travel consultant summarizing a client's trip intent.

Given these trip preferences, write a single concise paragraph (3-5 sentences) that captures the essence of this trip. Include the destination, travel dates, duration, budget level, group composition, and the specific mood or experience the travelers are seeking. Be specific and evocative — this summary will be shared with specialist travel agents who need to immediately understand what kind of trip to plan.

Trip preferences:
{prefs_json}

Respond with ONLY the paragraph, no preamble or formatting."""
