"""Apply probed/verified addresses to unified_ferments_locations.json."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.contact_fields import save_json_csv
from scrapers.unified_ferments import _norm_name

DATA_DIR = ROOT / "data"
UF_PATH = DATA_DIR / "unified_ferments_locations.json"
STAGING_CSV = DATA_DIR / "geocode_staging_review.csv"

DROP_NAMES = {
    _norm_name(n).upper()
    for n in (
        "WHITE TIGER",
        "WHITE TIGER TAVERN",
        "WINONA'S",
        "WINONA’S",
    )
}

STAGING_FIELDS = (
    "staging_address",
    "staging_city",
    "staging_confidence",
    "staging_provider",
    "staging_note",
    "suspected_duplicate_of",
)

VERIFIED_UPDATES: dict[str, dict[str, object]] = {
    "SAGA": {
        "address": "70 Pine St, 63rd Floor, New York, NY 10005, USA",
        "suburb": "Manhattan",
        "phone": "(212) 339-3963",
        "latitude": 40.7064733,
        "longitude": -74.0077415,
    },
    "THE GETAWAY 151": {
        "address": "743 Riverside Dr, New York, NY 10031, USA",
        "suburb": "Manhattan",
        "latitude": 40.8291278,
        "longitude": -73.9504324,
    },
    "MOONFLOWER": {
        "address": "201 West 11th Street, Manhattan, NY 10014, USA",
        "suburb": "Manhattan",
        "latitude": 40.7425,
        "longitude": -74.0088761,
    },
}


def normalize_key(name: str) -> str:
    return _norm_name(name).upper()


def clear_staging(row: dict) -> None:
    for key in STAGING_FIELDS:
        row.pop(key, None)


def promote_staging(row: dict) -> bool:
    staging = (row.get("staging_address") or "").strip()
    confidence = float(row.get("staging_confidence") or 0)
    if not staging or confidence < 0.85:
        return False
    row["address"] = staging if staging.endswith("USA") else f"{staging}, USA"
    suburb = (row.get("staging_city") or "").strip()
    if suburb:
        row["suburb"] = suburb
    row["verified"] = True
    row["needs_review"] = False
    row["geocode_status"] = "resolved"
    clear_staging(row)
    return True


def apply_manual(row: dict, fields: dict[str, object]) -> None:
    row.update(fields)
    row["verified"] = True
    row["needs_review"] = False
    row["geocode_status"] = "resolved"
    row["state"] = row.get("state") or "NY"
    clear_staging(row)


def export_staging_csv(rows: list[dict]) -> int:
    review_rows = []
    for row in rows:
        if row.get("verified"):
            continue
        if not row.get("needs_review") and not row.get("staging_address"):
            continue
        review_rows.append(
            {
                "name": row.get("name", ""),
                "brand": row.get("competitor", ""),
                "old_address": row.get("address") or "",
                "staging_address": row.get("staging_address", ""),
                "provider": row.get("staging_provider", ""),
                "confidence": row.get("staging_confidence", 0.0),
                "needs_review": row.get("needs_review", True),
                "note": row.get("staging_note", "") or row.get("suspected_duplicate_of", ""),
            }
        )
    review_rows.sort(key=lambda r: (r.get("confidence") is None, r.get("confidence", 0.0)))
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
    with STAGING_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(review_rows)
    return len(review_rows)


def main() -> None:
    rows: list[dict] = json.loads(UF_PATH.read_text(encoding="utf-8"))
    before = len(rows)

    kept: list[dict] = []
    dropped: list[str] = []
    for row in rows:
        key = normalize_key(row.get("name", ""))
        if key in DROP_NAMES:
            dropped.append(row.get("name", ""))
            continue
        kept.append(row)

    updated: list[str] = []
    promoted: list[str] = []

    for row in kept:
        key = normalize_key(row.get("name", ""))
        if key in VERIFIED_UPDATES:
            apply_manual(row, VERIFIED_UPDATES[key])
            updated.append(f"{row.get('name')} -> {row['address']}")
            continue
        if promote_staging(row):
            promoted.append(f"{row.get('name')} -> {row['address']}")

    save_json_csv(UF_PATH, kept)
    export_count = export_staging_csv(kept)

    print(f"Rows: {before} -> {len(kept)} (dropped {len(dropped)})")
    if dropped:
        print("Dropped:", ", ".join(dropped))
    if updated:
        print("Verified updates:")
        for line in updated:
            print(f"  - {line}")
    if promoted:
        print("Promoted from staging:")
        for line in promoted:
            print(f"  - {line}")
    print(f"Staging CSV: {export_count} row(s) -> {STAGING_CSV}")


if __name__ == "__main__":
    main()
