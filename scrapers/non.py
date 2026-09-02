"""Scrape NON stockists from the public find.non.world API."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

COMPETITOR = "NON"
STOCKISTS_URL = "https://find.non.world/api/stockists"
SOURCE_URL = "https://us.non.world/pages/store-locator"
USER_AGENT = "comp-drink/0.1 (+local competitor stockist research)"

# Known street addresses when API only returns city + region
ADDRESS_OVERRIDES: dict[str, dict[str, str]] = {
    "LAKE SIDE EMOTIONS": {
        "address": "113 Main St, Stony Brook, NY 11790",
        "suburb": "Stony Brook",
        "state": "NY",
        "phone": "(631) 675-2750",
    },
    "MAIDSTONE ARMS": {
        "address": "207 Main St, East Hampton, NY 11937",
        "suburb": "East Hampton",
        "state": "NY",
        "phone": "(631) 324-5006",
    },
    "THE TOWN CELLAR": {
        "address": "1089 Boston Post Rd, Darien, CT 06820",
        "suburb": "Darien",
        "state": "CT",
        "phone": "(203) 655-1031",
    },
    "LOVE EATS": {
        "address": "10 Amagansett Square Unit B, Amagansett, NY 11930",
        "suburb": "Amagansett",
        "state": "NY",
        "phone": "(631) 557-3038",
    },
    "MAIN STREET FARM": {
        "address": "36 Main St Unit B & C, Livingston Manor, NY 12758",
        "suburb": "Livingston Manor",
        "state": "NY",
        "phone": "(845) 439-4309",
        "website": "https://mainstreetfarm.com",
    },
    "REFRAME: A DRY SPOT": {
        "address": "Mobile bar — Willimantic, CT",
        "suburb": "Willimantic",
        "state": "CT",
        "venue_type": "Mobile Bar",
        "email": "@reframedryspot.com",
        "website": "https://www.reframedryspot.com/",
        "notes": "Frances — non-alcoholic mobile bar",
    },
    "HUDSON DRY": {
        "address": "421 Warren St, Hudson, NY 12534",
        "suburb": "Hudson",
        "state": "NY",
        "phone": "(518) 660-0421",
    },
}


def fetch_stockists(timeout: float = 60.0) -> dict[str, Any]:
    response = requests.get(
        STOCKISTS_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _address(suburb: str | None, region: str | None) -> str:
    parts = [p.strip() for p in (suburb or "", region or "") if p and str(p).strip()]
    return ", ".join(parts)


def normalize(payload: dict[str, Any]) -> list[dict[str, Any]]:
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for item in payload.get("stockists") or []:
        lat = item.get("lat")
        lng = item.get("lng")
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            continue
        if not (abs(lat_f) <= 90 and abs(lng_f) <= 180):
            continue

        suburb = (item.get("suburb") or "").strip()
        region = (item.get("region") or "").strip()
        rows.append(
            {
                "competitor": COMPETITOR,
                "source_id": str(item.get("id") or ""),
                "name": (item.get("name") or "").strip(),
                "address": _address(suburb, region),
                "suburb": suburb,
                "region": region,
                "venue_type": (item.get("type") or "").strip(),
                "latitude": lat_f,
                "longitude": lng_f,
                "source_url": SOURCE_URL,
                "scraped_at": scraped_at,
            }
        )
    return rows


def apply_address_overrides(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply manual street addresses and contact info for known stockists."""
    overrides = {k.upper(): v for k, v in ADDRESS_OVERRIDES.items()}
    for row in rows:
        key = (row.get("name") or "").strip().upper()
        if key not in overrides:
            continue
        if row.get("verified"):
            continue
        patch = overrides[key]
        row["address"] = patch["address"]
        if patch.get("suburb"):
            row["suburb"] = patch["suburb"]
        if patch.get("state"):
            row["state"] = patch["state"]
        if patch.get("phone"):
            row["phone"] = patch["phone"]
        if patch.get("website"):
            row["website"] = patch["website"]
        if patch.get("email"):
            row["email"] = patch["email"]
        if patch.get("venue_type"):
            row["venue_type"] = patch["venue_type"]
        if patch.get("notes"):
            row["notes"] = patch["notes"]
    return rows


def scrape() -> list[dict[str, Any]]:
    return apply_address_overrides(normalize(fetch_stockists()))


def save(rows: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "non_locations.json"
    csv_path = out_dir / "non_locations.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    import csv

    fieldnames = [
        "competitor",
        "source_id",
        "name",
        "address",
        "scraped_address",
        "suburb",
        "region",
        "venue_type",
        "latitude",
        "longitude",
        "source_url",
        "scraped_at",
        "state",
        "verified",
        "needs_review",
        "phone",
        "email",
        "website",
        "contact",
        "hours",
        "contact_source",
        "geocode_status",
        "notes",
    ]
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    json_path = root / "data" / "non_locations.json"

    from scrapers.location_merge import (
        apply_verified_and_review_flags,
        load_json_rows,
        merge_scraped_into_existing,
        non_row_key,
    )

    existing = load_json_rows(json_path)
    scraped = scrape()
    rows = merge_scraped_into_existing(existing, scraped, non_row_key)
    rows = apply_verified_and_review_flags(rows)
    json_path, csv_path = save(rows, root / "data")
    regions: dict[str, int] = {}
    for row in rows:
        regions[row["region"] or "?"] = regions.get(row["region"] or "?", 0) + 1
    print(f"Scraped {len(rows)} NON locations")
    print("Regions:", dict(sorted(regions.items(), key=lambda kv: (-kv[1], kv[0]))))
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
