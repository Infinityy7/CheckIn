"""Candidate repair: a specialist fills its own category instead of dropping."""

from __future__ import annotations

import json
import logging

from agents import RestaurantAgent


def _candidate(name: str, **overrides) -> dict:
    values = dict(
        name=name,
        description="A verified spot with a strong sense of place.",
        reasoning="It matches the travel profile.",
        estimated_cost="$40-$80",
        cost_min=40,
        cost_max=80,
        rating=4.7,
        review_count=900,
        location="Las Vegas Strip",
        image_search_query="las vegas dining",
    )
    values.update(overrides)
    return values


def test_missing_category_is_repaired_with_the_agents_own():
    raw = json.dumps({"recommendations": [
        _candidate("Delilah"), _candidate("Peppermill"), _candidate("Sparrow + Wolf"),
    ]})
    parsed = RestaurantAgent()._parse_and_validate(raw)

    assert len(parsed) == 3
    assert all(rec.category == "restaurant" for rec in parsed)


def test_wrong_explicit_category_is_overwritten_with_the_agents_own(caplog):
    raw = json.dumps({"recommendations": [
        _candidate("A", category="restaurant"),
        _candidate("B", category="restaurant"),
        _candidate("C", category="cafe"),
    ]})
    with caplog.at_level(logging.INFO, logger="agents.base"):
        parsed = RestaurantAgent()._parse_and_validate(raw)

    assert [rec.category for rec in parsed] == ["restaurant", "restaurant", "restaurant"]
    assert any("'cafe'" in record.getMessage() for record in caplog.records)
