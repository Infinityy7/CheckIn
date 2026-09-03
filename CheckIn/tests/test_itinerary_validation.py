"""Semantic validation of model-produced itineraries and the retry loop around it."""

from __future__ import annotations

import asyncio
import json

import pytest

import itinerary as itinerary_module
from itinerary import (
    ItineraryOmittedSelections,
    ItineraryValidationError,
    missing_recommendations,
    validate_itinerary,
)
from schemas import DayPlan, Itinerary, ItineraryItem, Recommendation, TripPreferences

PREFS = TripPreferences(
    destination="Kyoto",
    origin="Mumbai",
    start_date="2026-10-12",
    end_date="2026-10-14",
    budget_amount=2000,
    currency="USD",
    vibes=["culture"],
    group_type="couple",
    num_travelers=2,
)


def _rec(category: str, name: str) -> Recommendation:
    return Recommendation(
        id=f"{category}-{name.lower().replace(' ', '-')}",
        name=name,
        category=category,
        description="Controlled recommendation.",
        reasoning="Profile fit.",
        estimated_cost="$100",
        rating=4.6,
        location="Kyoto",
        image_search_query="Kyoto",
    )


SELECTED = [_rec("hotel", "Gion Garden House"), _rec("restaurant", "Kappo Sora")]


def _item(title: str, category: str = "activity", description: str = "A relaxed stop.") -> ItineraryItem:
    return ItineraryItem(
        time_slot="10:00 AM - 12:00 PM",
        title=title,
        description=description,
        category=category,
        cost_estimate="$20",
        location="Gion",
    )


def _day(number: int, date: str, items: list[ItineraryItem]) -> DayPlan:
    return DayPlan(day_number=number, date=date, theme=f"Day {number}", items=items)


def _valid() -> Itinerary:
    return Itinerary(
        trip_title="Kyoto in three breaths",
        trip_summary="Temples, tea, and time to wander.",
        days=[
            _day(1, "2026-10-12", [_item("Check in at Gion Garden House", "accommodation")]),
            _day(2, "2026-10-13", [_item("Dinner at Kappo Sora", "Restaurant")]),
            _day(3, "2026-10-14", [_item("Slow morning", "free_time"), _item("Check out of Gion Garden House", "accommodation")]),
        ],
    )


def test_valid_plan_passes_and_categories_are_normalized():
    plan = _valid()
    validate_itinerary(plan, PREFS, SELECTED)
    assert plan.days[1].items[0].category == "restaurant"
    assert missing_recommendations(plan, SELECTED) == []


def _wrong_date(plan: Itinerary) -> None:
    plan.days[1].date = "2026-10-15"


def _missing_day(plan: Itinerary) -> None:
    plan.days.pop()


def _extra_day(plan: Itinerary) -> None:
    plan.days.append(_day(4, "2026-10-15", [_item("Bonus")]))


def _unordered(plan: Itinerary) -> None:
    plan.days[1].day_number, plan.days[2].day_number = 3, 2


def _duplicate_number(plan: Itinerary) -> None:
    plan.days[2].day_number = 2


def _empty_day(plan: Itinerary) -> None:
    plan.days[2].items = []


def _non_iso_date(plan: Itinerary) -> None:
    plan.days[0].date = "Oct 12, 2026"


def _bad_category(plan: Itinerary) -> None:
    plan.days[0].items[0].category = "sightseeing"


def _no_days(plan: Itinerary) -> None:
    plan.days = []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_wrong_date, "must be 2026-10-13"),
        (_missing_day, "has 2 days but the trip runs 2026-10-12 to 2026-10-14"),
        (_extra_day, "has 4 days"),
        (_unordered, "day_number must run 1..3 in order"),
        (_duplicate_number, "position 3 has day_number 2"),
        (_empty_day, "Day 3 has no items"),
        (_non_iso_date, "non-ISO date"),
        (_bad_category, "unknown category 'sightseeing'"),
        (_no_days, "no days"),
    ],
)
def test_structural_problems_are_rejected_with_specific_messages(mutate, message):
    plan = _valid()
    mutate(plan)
    with pytest.raises(ItineraryValidationError, match=message):
        validate_itinerary(plan, PREFS, SELECTED)


def test_omitted_selection_is_named_case_insensitively():
    plan = _valid()
    plan.days[1].items[0] = _item("Dinner somewhere nice", "restaurant")
    with pytest.raises(ItineraryOmittedSelections, match="Kappo Sora") as excinfo:
        validate_itinerary(plan, PREFS, SELECTED)
    assert excinfo.value.names == ["Kappo Sora"]

    mentioned_in_description = _valid()
    mentioned_in_description.days[1].items[0] = _item("Dinner", "restaurant", "Counter seats at KAPPO SORA.")
    validate_itinerary(mentioned_in_description, PREFS, SELECTED)


def _stub_generate(monkeypatch, responses: list[str]) -> list[str]:
    prompts: list[str] = []

    async def fake_generate_text(prompt: str, **_kwargs):
        prompts.append(prompt)
        return responses[min(len(prompts) - 1, len(responses) - 1)]

    monkeypatch.setattr(itinerary_module, "generate_text", fake_generate_text)
    monkeypatch.setattr(itinerary_module, "MAX_AGENT_RETRIES", 2)
    return prompts


def test_rejected_plan_is_retried_with_a_targeted_correction(monkeypatch):
    incomplete = _valid()
    incomplete.days[1].items[0] = _item("Dinner somewhere nice", "restaurant")
    prompts = _stub_generate(monkeypatch, [json.dumps(incomplete.model_dump()), json.dumps(_valid().model_dump())])

    result = asyncio.run(itinerary_module.generate_itinerary(PREFS, "brief", SELECTED))

    assert result.trip_title == "Kyoto in three breaths"
    assert len(prompts) == 2
    assert "rejected because" in prompts[1]
    assert "Kappo Sora" in prompts[1]
    assert "rejected because" not in prompts[0]


def test_persistent_omission_fails_with_a_clear_reason(monkeypatch):
    incomplete = _valid()
    incomplete.days[1].items[0] = _item("Dinner somewhere nice", "restaurant")
    prompts = _stub_generate(monkeypatch, [json.dumps(incomplete.model_dump())])

    with pytest.raises(RuntimeError, match="omitted selected recommendations: Kappo Sora"):
        asyncio.run(itinerary_module.generate_itinerary(PREFS, "brief", SELECTED))
    assert len(prompts) == 2


def test_wrong_dates_exhaust_retries_with_the_generic_failure(monkeypatch):
    shifted = _valid()
    _wrong_date(shifted)
    prompts = _stub_generate(monkeypatch, [json.dumps(shifted.model_dump())])

    with pytest.raises(RuntimeError, match="failed after 2 bounded attempts"):
        asyncio.run(itinerary_module.generate_itinerary(PREFS, "brief", SELECTED))
    assert "must be 2026-10-13" in prompts[1]


def test_unparseable_output_keeps_the_short_output_hint(monkeypatch):
    prompts = _stub_generate(monkeypatch, ["not json at all", json.dumps(_valid().model_dump())])

    result = asyncio.run(itinerary_module.generate_itinerary(PREFS, "brief", SELECTED))

    assert result.days[0].date == "2026-10-12"
    assert "invalid or incomplete" in prompts[1]


def test_selected_names_match_despite_punctuation_and_diacritics():
    selected = [_rec("restaurant", "L'Atelier de Joël"), _rec("hotel", "Sowaka-Gion")]
    plan = Itinerary(
        trip_title="Kyoto",
        trip_summary="Calm.",
        days=[_day(1, "2026-10-12", [
            _item("Dinner at L’Atelier de Joel", category="restaurant"),
            _item("Check in at Sowaka Gion", category="accommodation"),
        ])],
    )
    assert missing_recommendations(plan, selected) == []
    assert missing_recommendations(plan, [_rec("activity", "Fushimi Inari")]) == ["Fushimi Inari"]
