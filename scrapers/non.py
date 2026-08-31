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


def scrape() -> list[dict[str, Any]]:
    return normalize(fetch_stockists())


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
        "suburb",
        "region",
        "venue_type",
        "latitude",
        "longitude",
        "source_url",
        "scraped_at",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    rows = scrape()
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
