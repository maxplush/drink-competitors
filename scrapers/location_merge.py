"""Merge scraped location rows with existing JSON; protect verified addresses."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

# Canonical verified corrections (27 venue keys from migration).
VERIFIED_NAME_KEYS = frozenset(
    {
        "BLANCA",
        "FORT DEFIANCE",
        "NARO",
        "RUFFIAN",
        "SAGA",
        "WINONAS",
        "RHODORA",
        "SOMM TIME",
        "MISSION CHINESE",
        "NICHE NICHE",
        "HANA MAKGEOLLI",
        "FULGURANCES",
        "BATHHOUSE",
        "BAR MERIDIAN",
        "ODDLY ENOUGH",
        "WEN WEN",
        "THE FLY",
        "CHERRY ON TOP",
        "PEOPLES WINE",
        "AMAN NEW YORK",
        "CROWN SHY",
        "SOHO GRAND HOTEL",
        "SMITH & MILLS",
        "BOTTLEROCKET",
        "EDITION HOTELS",
        "CLAUDETTE",
        "GAGE & TOLLNER",
    }
)

VERIFIED_NAME_ALIASES: dict[str, frozenset[str]] = {
    "BATHHOUSE": frozenset({"A BATHHOUSE"}),
    "RHODORA": frozenset({"RHODORA WINE BAR"}),
    "FULGURANCES": frozenset({"FULGURANCES LAUNDROMAT"}),
    "WINONAS": frozenset({"WINONA'S", "WINONA’S"}),
    "PEOPLES WINE": frozenset({"PEOPLE'S WINE", "PEOPLE’S WINE"}),
    "BOTTLEROCKET": frozenset({"BOTTLEROCKET WINE & SPIRIT"}),
    "THE GETAWAY 151": frozenset({"The Getaway 151"}),
}

# Rows that must stay flagged for manual review (not in the 27 verified set).
NEEDS_REVIEW_NAME_KEYS = frozenset(
    {
        "LITTLE FLOWER",
        "WHITE TIGER",
        "AS IS",
        "PEARL STREET SUPPER CLUB",
        "EXTRA EXTRA PIZZA",
        "THE GETAWAY 151",
        "MOONFLOWER",
        "KINDRED FARE",
    }
)

# Verified outside the 27 migration keys (staging / manual corrections).
ADDITIONAL_VERIFIED_NAME_KEYS = frozenset(
    {
        "LIL DEB'S OASIS",
        "LIL DEB’S OASIS",
    }
)

PROTECTED_WHEN_VERIFIED = frozenset({"address", "suburb", "state", "latitude", "longitude"})


def norm_name(name: str) -> str:
    s = (name or "").replace("\u2019", "'").replace("\u2018", "'").strip()
    s = re.sub(r"[^A-Za-z0-9'& ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().upper()


def verified_name_key(name: str) -> str | None:
    n = norm_name(name)
    if n in VERIFIED_NAME_KEYS:
        return n
    for key, aliases in VERIFIED_NAME_ALIASES.items():
        if n in {norm_name(a) for a in aliases}:
            return key
    return None


def is_verified_name(name: str) -> bool:
    return verified_name_key(name) is not None


def is_needs_review_name(name: str) -> bool:
    return norm_name(name) in NEEDS_REVIEW_NAME_KEYS


def is_additional_verified_name(name: str) -> bool:
    n = norm_name(name)
    return n in {norm_name(k) for k in ADDITIONAL_VERIFIED_NAME_KEYS}


def ensure_schema(row: dict[str, Any]) -> dict[str, Any]:
    if "verified" not in row:
        row["verified"] = False
    if "needs_review" not in row:
        row["needs_review"] = False
    return row


def count_verified(rows: list[dict[str, Any]]) -> int:
    return sum(1 for r in rows if r.get("verified") is True)


def uf_row_key(row: dict[str, Any]) -> tuple[str, str]:
    return (row.get("competitor") or "", norm_name(row.get("name") or ""))


def non_row_key(row: dict[str, Any]) -> tuple[str, str]:
    sid = (row.get("source_id") or "").strip()
    return (row.get("competitor") or "", sid if sid else norm_name(row.get("name") or ""))


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected list")
    return [ensure_schema(dict(r)) for r in payload]


def merge_scraped_into_existing(
    existing: list[dict[str, Any]],
    scraped: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], tuple[str, str]],
) -> list[dict[str, Any]]:
    """Merge scrape output into existing rows; never overwrite verified address/city/state."""
    existing_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing:
        ensure_schema(row)
        existing_by_key[key_fn(row)] = row

    scraped_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in scraped:
        scraped_by_key[key_fn(row)] = ensure_schema(dict(row))

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for key, old in existing_by_key.items():
        seen.add(key)
        new = scraped_by_key.get(key)
        if new is None:
            merged.append(old)
            continue
        merged.append(_merge_pair(old, new))

    for key, new in scraped_by_key.items():
        if key in seen:
            continue
        merged.append(ensure_schema(dict(new)))

    return merged


def _merge_pair(existing: dict[str, Any], scraped: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    scraped = ensure_schema(scraped)

    scraped_addr = scraped.get("address") or ""
    if scraped_addr:
        merged["scraped_address"] = scraped_addr

    if merged.get("verified"):
        for field in PROTECTED_WHEN_VERIFIED:
            if field in existing and existing.get(field) is not None:
                merged[field] = existing[field]
    else:
        for key, value in scraped.items():
            if key in ("verified", "needs_review"):
                continue
            if value is not None and value != "":
                merged[key] = value

    merged["verified"] = bool(existing.get("verified"))
    if existing.get("verified"):
        merged["needs_review"] = False
    else:
        merged["needs_review"] = bool(
            existing.get("needs_review") or scraped.get("needs_review")
        )

    return ensure_schema(merged)


def apply_verified_and_review_flags(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Set verified / needs_review from canonical name lists."""
    for row in rows:
        ensure_schema(row)
        name = row.get("name") or ""
        if is_verified_name(name) or is_additional_verified_name(name):
            row["verified"] = True
            row["needs_review"] = False
        elif is_needs_review_name(name):
            row["verified"] = False
            row["needs_review"] = True
    return rows


def count_verified_in_dashboard(html_path: Path) -> int:
    if not html_path.exists():
        return 0
    text = html_path.read_text(encoding="utf-8")
    marker = "const DATA = "
    start = text.index(marker) + len(marker)
    data, _ = json.JSONDecoder().raw_decode(text, start)
    return sum(1 for r in data if r.get("verified") is True)


def parse_dashboard_data(html_path: Path) -> list[dict[str, Any]]:
    text = html_path.read_text(encoding="utf-8")
    marker = "const DATA = "
    start = text.index(marker) + len(marker)
    data, _ = json.JSONDecoder().raw_decode(text, start)
    return data
