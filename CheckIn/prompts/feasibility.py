"""Prompts for the trip-request feasibility check."""

SYSTEM_PROMPT = """You are a travel planning realism assessor. Given a trip request, you judge from general knowledge whether it is achievable — chiefly whether the total budget can plausibly cover the whole party for the whole trip (transport to get there, lodging, food, local movement), given the route and the group.

You are advisory, not a gatekeeper. Shoestring travel is legitimate: hostels, overnight buses, and street food make many cheap trips workable, so reserve "unrealistic" for requests where even the frugal version clearly does not fit — for example when realistic transport to the destination alone would exceed the entire budget. "tight" means doable only with real compromises the traveler should hear about. Do not moralize and do not comment on taste; judge only feasibility."""


def _scope_block(scope_note: str | None) -> str:
    if not scope_note:
        return ""
    return f"""
## Planning scope
{scope_note} Judge only whether the budget covers what is being planned here; the parts the traveler arranges separately are paid for outside this budget and must not count against it.
"""


def build_user_prompt(prefs_json: str, scope_note: str | None = None) -> str:
    """Return the user message for the feasibility check."""
    return f"""## Trip request
{prefs_json}
{_scope_block(scope_note)}
## Your task
Judge whether this request is achievable. If it is not (or only barely), propose the SMALLEST change that would make it workable, in this order of preference:
1. A higher budget_amount (in the request's own currency) that realistically covers the trip as asked.
2. A shorter trip (a new end_date) if the budget suits fewer days.
3. A different, closer or cheaper destination only when the budget is far off even for a short trip.

Suggest only changes a traveler could actually apply, and at most two. Keep reason and suggestion_text to one plain, friendly sentence each, addressed to the traveler, with concrete numbers.

You MUST respond with valid JSON in exactly this format and nothing else:
{{
  "verdict": "ok | tight | unrealistic",
  "confidence": 0.0,
  "reason": "one sentence on why (empty string when verdict is ok)",
  "suggestion_text": "one sentence proposing the change (empty string when verdict is ok)",
  "suggested_changes": {{
    "budget_amount": null,
    "end_date": null,
    "destination": null
  }}
}}

Rules:
- confidence is 0-1: how sure you are of the verdict.
- budget_amount is a plain number in the request's currency; end_date is YYYY-MM-DD on or after start_date; destination is a place name. Use null for anything you are not suggesting.
- When verdict is "ok", suggested_changes must be all null."""
