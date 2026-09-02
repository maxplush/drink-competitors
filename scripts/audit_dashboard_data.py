#!/usr/bin/env python3
"""Parse const DATA from competitors_dashboard.html and report audit fields."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "data" / "competitors_dashboard.html"

BAD_ADDRESS_MARKERS = (
    "Hair Saga",
    "Taekwondo",
    "Playground",
    "Casa Blanca",
    "Oversea Chinese",
)


def load_dashboard_data() -> list[dict]:
    text = DASHBOARD.read_text(encoding="utf-8")
    marker = "const DATA = "
    start = text.index(marker) + len(marker)
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(text, start)
    if not isinstance(data, list):
        raise ValueError(f"Expected list, got {type(data)}")
    return data


def find_10000_matches(record: dict) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for key, value in record.items():
        if value is None:
            continue
        s = str(value)
        if "10000" in s:
            matches.append((key, s))
    return matches


def classify_10000(value: str) -> str:
    if re.search(r"\b10000\b", value):
        return "likely ZIP/postcode"
    if re.search(r"10000", value):
        return "likely coordinate/float substring"
    return "unknown"


def main() -> None:
    data = load_dashboard_data()

    print("=== 1. CROWN SHY record(s), full raw ===")
    crown = [r for r in data if (r.get("name") or "").upper() == "CROWN SHY"]
    if not crown:
        print("(none)")
    for row in crown:
        print(json.dumps(row, ensure_ascii=False, indent=2))
    print()

    print("=== 2. Records containing '10000' (name + field + classification) ===")
    any_10000 = False
    for row in data:
        hits = find_10000_matches(row)
        if not hits:
            continue
        any_10000 = True
        name = row.get("name", "")
        for field, value in hits:
            print(f"  {name!r} | {field}={value!r} | {classify_10000(value)}")
    if not any_10000:
        print("(none)")
    print()

    print("=== 3. address contains County or Town of ===")
    county_town = [
        r
        for r in data
        if "County" in (r.get("address") or "") or "Town of" in (r.get("address") or "")
    ]
    print(len(county_town))
    print()

    print("=== 4. address contains bad geocode markers (expect 0) ===")
    for marker in BAD_ADDRESS_MARKERS:
        n = sum(1 for r in data if marker in (r.get("address") or ""))
        print(f"  {marker!r}: {n}")
    total_bad = sum(
        1
        for r in data
        if any(m in (r.get("address") or "") for m in BAD_ADDRESS_MARKERS)
    )
    print(f"  total distinct records: {total_bad}")
    print()

    print("=== 5. totals ===")
    print(f"  total records: {len(data)}")
    verified_count = sum(1 for r in data if r.get("verified") is True)
    print(f"  verified=true: {verified_count}")
    print(f"  'verified' key present in any row: {any('verified' in r for r in data)}")
    print()

    print("=== 6. CULINARY PURSUITS / QUALITY BURGER ===")
    targets = {"CULINARY PURSUITS", "QUALITY BURGER"}
    for name in targets:
        found = [r for r in data if (r.get("name") or "").upper() == name]
        print(f"  {name}: {'EXISTS' if found else 'NOT FOUND'} ({len(found)} row(s))")


if __name__ == "__main__":
    main()
