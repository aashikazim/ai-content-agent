"""Canonical request fingerprints and stable domain IDs (no side effects)."""

import hashlib
import json
from datetime import date
from typing import Any
from uuid import UUID, uuid5

NAMESPACE = UUID("f97bc88c-5ce7-5a83-b58a-5aab0d0da80a")


def canonical_json(value: Any) -> str:
    """Only JSON values; reject NaN and non-string keys rather than coerce them."""
    def check(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(scope: str, *parts: str | int) -> str:
    # Structured encoding avoids ambiguous concatenations such as (a:b,c)/(a,b:c).
    return str(uuid5(NAMESPACE, canonical_json([scope, *parts])))


def daily_slot_id(plan_date: date, lane: str, slot: int) -> str:
    if type(slot) is not int or slot < 1:
        raise ValueError("Slot must be a positive integer")
    return stable_id("daily_slot", plan_date.isoformat(), lane, slot)


def episode_id(series_id: str, ep_no: int) -> str:
    if type(ep_no) is not int or not 1 <= ep_no <= 30:
        raise ValueError("Episode number must be 1–30")
    return stable_id("series_episode", series_id, ep_no)
