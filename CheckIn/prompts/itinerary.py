"""Prompt for the itinerary generation step."""


def _exact_choices_block(exact_choices_json: str | None) -> str:
    if not exact_choices_json:
        return ""
    return f"""
## Exact booked/quoted choices
The traveler has already saved these exact supplier options. They are fixed inputs, not suggestions:
{exact_choices_json}

Use exactly these rooms and flights. Never substitute a different room type, rate plan, carrier, flight number, route, or departure time. Where a return leg is marked unknown, do not invent one; describe the return day without specific flight details.
"""


def build_itinerary_prompt(
    prefs_json: str,
    context_brief: str,
    selected_recommendations_json: str,
    exact_choices_json: str | None = None,
) -> str:
    """Return the user message for itinerary generation."""
    return f"""## Trip Context
{context_brief}

## Trip Preferences
{prefs_json}

## Selected Recommendations
The traveler has chosen the following from our expert agents' research:
{selected_recommendations_json}
{_exact_choices_block(exact_choices_json)}
## Your Task
Create a detailed, day-by-day itinerary that weaves the selected recommendations into a coherent, enjoyable trip. You are an expert itinerary architect — think like a concierge who knows the destination intimately.

### Rules
1. **Every selected recommendation must appear** in the itinerary at least once, using its exact `name` verbatim in the item title or description.
2. **Schedule logically**: mornings for active/outdoor activities, afternoons for exploration or culture, evenings for dining and nightlife. Respect venue operating hours.
3. **Cluster geographically**: group activities in the same neighborhood on the same day to minimize transit time.
4. **Add transitions**: between activities, note approximate travel time and method (e.g., "15-minute walk through the historic quarter" or "Take the metro Line 3, ~20 min").
5. **Include free time**: don't overschedule. Leave gaps for spontaneous exploration, rest, or lingering at places they love.
6. **Incorporate transport advice**: use the selected transport strategy throughout. Reference specific passes, apps, or methods.
7. **Add local tips**: practical notes like "arrive before 10 AM to beat crowds" or "the sunset view from here is spectacular around 6 PM."
8. **First day / last day awareness**: account for arrival/departure logistics. Don't schedule a 9 AM activity on arrival day if the flight likely lands midday.
9. **Accommodation check-in/out**: note hotel check-in on day 1 and check-out on the last day.
10. **Exact choices are fixed**: when an exact room rate or flight offer is listed above, the itinerary must use it as-is.

### Structure requirements (the plan is rejected if any fails)
- `days` covers every calendar date from `start_date` through `end_date` inclusive, one entry per date, in order. No missing, extra, or repeated dates.
- `day_number` runs 1, 2, 3 ... in the same order as the dates.
- `date` is ISO `YYYY-MM-DD`.
- Every day has at least one item. No empty days.
- `category` is exactly one of: accommodation, activity, restaurant, transport, free_time.

### Important
- Keep descriptions concise (1-2 sentences per item). Do NOT write lengthy paragraphs.
- Aim for 4-6 items per day, not more.
- Tips should be one short sentence or null.

### Response Format
Respond with valid JSON ONLY, no other text:
{{
  "trip_title": "A catchy, evocative title for this specific trip",
  "trip_summary": "2-3 sentences capturing the spirit of this itinerary — what makes it special and what the traveler can look forward to.",
  "days": [
    {{
      "day_number": 1,
      "date": "YYYY-MM-DD",
      "theme": "A short thematic title for the day (e.g., 'Arrival & First Tastes')",
      "items": [
        {{
          "time_slot": "2:00 PM - 3:00 PM",
          "title": "Activity or event title",
          "description": "What happens during this slot. Be specific and engaging.",
          "category": "accommodation | activity | restaurant | transport | free_time",
          "cost_estimate": "$X or Free",
          "location": "Specific location or neighborhood",
          "tip": "Optional local tip or practical note"
        }}
      ]
    }}
  ]
}}"""
