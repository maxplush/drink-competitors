"""
Re-geocode rows with needs_review or missing address; write staging fields only.

Skips verified rows. Persists staging_* columns on location JSON rows and exports
a review CSV sorted by confidence (weakest first).
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.contact_fields import save_json_csv
from scrapers.unified_ferments import (
    CITY_HINTS,
    LOCATION_OVERRIDES,
    MANUAL_ADDRESSES,
    NAME_ALIASES,
    USER_AGENT,
    _alias_or_manual,
    _norm_name,
)
from scrapers.venue_geocode import (
    GeocodeHit,
    NY_STATE_VIEWBOX,
    resolve_manual_street,
    resolve_venue_name,
)

DATA_DIR = ROOT / "data"
EXPORT_CSV = DATA_DIR / "geocode_staging_review.csv"

STATE_QUERY_AREAS: dict[str, str] = {
    "NY": "New York, NY, USA",
    "CA": "California, USA",
    "NJ": "New Jersey, USA",
    "DC": "Washington, DC, USA",
    "MA": "Massachusetts, USA",
    "NC": "North Carolina, USA",
    "SC": "South Carolina, USA",
    "VT": "Vermont, USA",
}

VERIFIED_CORRECTIONS: dict[str, str] = {
    "LIL DEB'S OASIS": "747 Columbia St, Hudson, NY 12534",
    "LIL DEB’S OASIS": "747 Columbia St, Hudson, NY 12534",
    "KINDRED FARE": "512 Hamilton St, Geneva, NY 14456",
    "AS IS": "734 10th Ave, New York, NY 10019",
    "EXTRA EXTRA PIZZA": "549 W Utica St, Buffalo, NY 14213",
    "PEARL STREET SUPPER CLUB": "147 Front St, Brooklyn, NY 11201",
}

DUPLICATE_REVIEW: dict[str, str] = {
    "WHITE TIGER": "WHITE TIGER TAVERN",
}

NO_DOWNGRADE_CONFIDENCE = 0.85
STREET_NUMBER_RE = re.compile(r"\d")

FORCE_REVIEW_NAMES = frozenset({"LITTLE FLOWER"})


def normalize_name(name: str) -> str:
    s = (name or "").upper()
    for ch in ("’", "`", "´"):
        s = s.replace(ch, "'")
    s = re.sub(r"[^A-Z0-9'& ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def should_regeocode(row: dict[str, Any]) -> bool:
    if row.get("verified"):
        return False
    addr = row.get("address")
    if addr is None or str(addr).strip() == "":
        return True
    return row.get("needs_review") is True


def infer_state(row: dict[str, Any]) -> str:
    state = (row.get("state") or "").strip().upper()
    if state:
        return state
    address = (row.get("address") or "").upper()
    if ", NY" in address or "NEW YORK" in address or "BROOKLYN" in address:
        return "NY"
    if ", CA" in address or "CALIFORNIA" in address:
        return "CA"
    return ""


def infer_query_area(row: dict[str, Any], state: str) -> str:
    if state in STATE_QUERY_AREAS:
        return STATE_QUERY_AREAS[state]
    region = (row.get("region") or "").strip()
    if region and region != "US":
        return f"{region}, USA"
    suburb = (row.get("suburb") or "").strip()
    if suburb:
        return f"{suburb}, USA"
    return "United States"


def manual_street_for_name(name: str) -> str | None:
    manual = _alias_or_manual(MANUAL_ADDRESSES, name)
    if manual:
        return manual
    n = _norm_name(name)
    for entries in LOCATION_OVERRIDES.values():
        for entry in entries:
            if _norm_name(entry.get("name", "")) == n and entry.get("address"):
                return entry["address"]
    return None


def parse_street_line(street_line: str) -> tuple[str, str, str, str]:
    m = re.match(
        r"^(.+?),\s*([^,]+),\s*([A-Z]{2})\s+(\d{5})\s*$",
        street_line.strip(),
    )
    if not m:
        return f"{street_line}, USA", "", "NY", ""
    street, suburb, state, zip_code = m.groups()
    return f"{street}, {suburb}, {state} {zip_code}, USA", suburb, state, zip_code


def address_has_street_number(addr: str) -> bool:
    return bool(STREET_NUMBER_RE.search(addr or ""))


def find_row_by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    target = normalize_name(name)
    for row in rows:
        if normalize_name(row.get("name", "")) == target:
            return row
    return None


def apply_verified_correction(
    row: dict[str, Any],
    street_line: str,
    session: requests.Session,
) -> bool:
    norm = normalize_name(row.get("name", ""))
    if norm not in {normalize_name(k) for k in VERIFIED_CORRECTIONS}:
        return False

    _, _, _, target_zip = parse_street_line(street_line)
    if row.get("verified") and target_zip and target_zip in (row.get("address") or ""):
        return False

    address, suburb, state, _ = parse_street_line(street_line)
    hit = resolve_manual_street(
        street_line,
        state or "NY",
        session,
        NY_STATE_VIEWBOX,
    )
    row["address"] = hit.address if hit else address
    row["suburb"] = (hit.suburb if hit else None) or suburb
    row["state"] = state or "NY"
    if hit:
        row["latitude"] = hit.latitude
        row["longitude"] = hit.longitude
        row["geocode_status"] = "resolved"
    row["verified"] = True
    row["needs_review"] = False
    clear_staging(row)
    row.pop("staging_note", None)
    row.pop("suspected_duplicate_of", None)
    return True


def build_queries(name: str, query_area: str, state: str) -> list[str]:
    search_name = _alias_or_manual(NAME_ALIASES, name) or name
    areas = [query_area] + [
        h for h in CITY_HINTS.get(state.upper(), []) if h != query_area
    ]
    queries: list[str] = []
    manual = manual_street_for_name(name)
    if manual:
        queries.append(manual)
    for area in areas:
        queries.extend(
            [
                f"{search_name}, {area}",
                f"{search_name} restaurant, {area}",
                f"{search_name} wine bar, {area}",
                f"{search_name} bar, {area}",
            ]
        )
    return queries


def clear_staging(row: dict[str, Any]) -> None:
    row["staging_address"] = ""
    row["staging_city"] = ""
    row["staging_confidence"] = 0.0
    row["staging_provider"] = ""


def apply_staging(
    row: dict[str, Any],
    hit: GeocodeHit | None,
    *,
    staging_needs_review: bool | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    old_addr = row.get("address") or ""
    existing_staging = row.get("staging_address") or ""

    if hit and address_has_street_number(old_addr) and hit.confidence < NO_DOWNGRADE_CONFIDENCE:
        row["staging_address"] = existing_staging
        row["staging_city"] = row.get("staging_city") or ""
        row["staging_confidence"] = hit.confidence
        row["staging_provider"] = hit.provider
        review = True
        row["staging_note"] = note or "no_downgrade: confidence below 0.85"
    elif hit:
        row["staging_address"] = hit.address
        row["staging_city"] = hit.suburb
        row["staging_confidence"] = hit.confidence
        row["staging_provider"] = hit.provider
        review = staging_needs_review if staging_needs_review is not None else hit.needs_review
        if note:
            row["staging_note"] = note
    else:
        clear_staging(row)
        review = staging_needs_review if staging_needs_review is not None else True
        if note:
            row["staging_note"] = note

    return export_row(row, review)


def export_row(row: dict[str, Any], needs_review: bool) -> dict[str, Any]:
    return {
        "name": row.get("name", ""),
        "brand": row.get("competitor", ""),
        "old_address": row.get("address") or "",
        "staging_address": row.get("staging_address", ""),
        "provider": row.get("staging_provider", ""),
        "confidence": row.get("staging_confidence", 0.0),
        "needs_review": needs_review,
        "note": row.get("staging_note", "") or row.get("suspected_duplicate_of", ""),
    }


def handle_duplicate_review(
    row: dict[str, Any],
    all_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    norm = normalize_name(row.get("name", ""))
    duplicate_of = DUPLICATE_REVIEW.get(norm)
    if not duplicate_of:
        return None
    other = find_row_by_name(all_rows, duplicate_of)
    if not other:
        return None

    row["needs_review"] = True
    row["suspected_duplicate_of"] = duplicate_of
    clear_staging(row)
    row["staging_note"] = f"suspected duplicate of {duplicate_of} — merge review"
    return export_row(row, True)


def geocode_row(
    row: dict[str, Any],
    session: requests.Session,
    pause: float = 1.05,
) -> GeocodeHit | None:
    name = row.get("name", "")
    state = infer_state(row)
    if not state:
        return None
    query_area = infer_query_area(row, state)
    queries = build_queries(name, query_area, state)
    manual = manual_street_for_name(name)
    return resolve_venue_name(
        name,
        query_area,
        state,
        session,
        queries,
        pause=pause,
        use_nominatim=True,
        manual_street=manual,
    )


def apply_verified_batch(
    rows: list[dict[str, Any]],
    session: requests.Session,
) -> list[str]:
    applied: list[str] = []
    seen: set[str] = set()
    for key, street_line in VERIFIED_CORRECTIONS.items():
        norm = normalize_name(key)
        if norm in seen:
            continue
        seen.add(norm)
        row = find_row_by_name(rows, key)
        if row and apply_verified_correction(row, street_line, session):
            applied.append(f"{row.get('competitor')}|{row.get('name')} -> {row['address']}")
    return applied


def run(pause: float = 1.05) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    review_rows: list[dict[str, Any]] = []
    changed_files: list[str] = []
    verified_applied: list[str] = []

    for path in sorted(DATA_DIR.glob("*_locations.json")):
        rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        file_changed = False

        for label in apply_verified_batch(rows, session):
            verified_applied.append(label)
            file_changed = True

        targets = [r for r in rows if should_regeocode(r)]
        if not targets and not file_changed:
            continue

        if targets:
            print(f"{path.name}: re-geocoding {len(targets)} row(s)...")

        for row in targets:
            norm = normalize_name(row.get("name", ""))

            dup_export = handle_duplicate_review(row, rows)
            if dup_export:
                review_rows.append(dup_export)
                file_changed = True
                print(f"  {row.get('name')}: flagged suspected duplicate")
                continue

            hit = geocode_row(row, session, pause=pause)

            if norm in FORCE_REVIEW_NAMES:
                row["needs_review"] = True
                clear_staging(row)
                row["staging_note"] = "unconfirmed match — manual review required"
                review_rows.append(export_row(row, True))
            else:
                review_rows.append(apply_staging(row, hit))

            file_changed = True
            print(
                f"  {row.get('name')}: "
                f"conf={row.get('staging_confidence', 0):.2f} "
                f"provider={row.get('staging_provider') or 'none'}"
            )

        if file_changed:
            save_json_csv(path, rows)
            changed_files.append(path.name)

    if verified_applied:
        print(f"Applied {len(verified_applied)} verified address(es):")
        for line in verified_applied:
            print(f"  - {line}")

    review_rows.sort(key=lambda r: (r.get("confidence") is None, r.get("confidence", 0.0)))

    EXPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "name",
        "brand",
        "old_address",
        "staging_address",
        "provider",
        "confidence",
        "needs_review",
        "note",
    ]
    with EXPORT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(review_rows)

    print(f"\nUpdated JSON: {', '.join(changed_files) or 'none'}")
    print(f"Exported {len(review_rows)} row(s) -> {EXPORT_CSV}")
    return review_rows


def main() -> None:
    rows = run()
    if not rows:
        print("No rows matched (needs_review or null address, not verified).")


if __name__ == "__main__":
    main()
