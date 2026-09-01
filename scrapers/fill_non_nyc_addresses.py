"""Fill street addresses for NON New York locations via reverse geocoding."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

import requests

USER_AGENT = "comp-drink/0.1 (+local competitor stockist research)"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "non_nyc_reverse_cache.json"
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "non_locations.json"

NYC_SUBURBS = {
    "NEW YORK",
    "BROOKLYN",
    "QUEENS",
    "BRONX",
    "MANHATTAN",
    "STATEN ISLAND",
    "LONG ISLAND CITY",
    "WILLIAMSBURG",
    "ASTORIA",
}


def in_nyc_bbox(lat: float, lng: float) -> bool:
    return 40.49 <= lat <= 40.92 and -74.26 <= lng <= -73.70


def is_nyc_row(row: dict[str, Any]) -> bool:
    suburb = (row.get("suburb") or "").strip().upper()
    if suburb in NYC_SUBURBS or any(s in suburb for s in NYC_SUBURBS):
        return True
    try:
        return in_nyc_bbox(float(row["latitude"]), float(row["longitude"]))
    except (KeyError, TypeError, ValueError):
        return False


def format_address(addr: dict[str, Any], display_name: str) -> str:
    number = (addr.get("house_number") or "").strip()
    road = (addr.get("road") or addr.get("pedestrian") or addr.get("footway") or "").strip()
    neighbourhood = (
        addr.get("neighbourhood")
        or addr.get("suburb")
        or addr.get("quarter")
        or addr.get("city_district")
        or ""
    ).strip()
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or ""
    ).strip()
    state = (addr.get("state") or "").strip()
    postcode = (addr.get("postcode") or "").strip()

    street = " ".join(p for p in (number, road) if p).strip()
    parts = [p for p in (street, neighbourhood, city, state, postcode) if p]
    if street:
        # Prefer a compact US-style line
        city_state_zip = ", ".join(p for p in (city or neighbourhood, state) if p)
        if postcode:
            city_state_zip = f"{city_state_zip} {postcode}".strip() if city_state_zip else postcode
        return ", ".join(p for p in (street, city_state_zip) if p)
    if parts:
        return ", ".join(parts)
    return display_name.split(",")[0].strip() if display_name else ""


def load_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def reverse_geocode(
    lat: float,
    lng: float,
    session: requests.Session,
    cache: dict[str, Any],
    pause: float = 1.05,
) -> str | None:
    key = f"{lat:.6f},{lng:.6f}"
    if key in cache:
        return cache[key]

    try:
        res = session.get(
            NOMINATIM_REVERSE,
            params={
                "lat": lat,
                "lon": lng,
                "format": "jsonv2",
                "addressdetails": 1,
                "zoom": 18,
            },
            timeout=40,
        )
        res.raise_for_status()
        data = res.json()
    except requests.RequestException:
        time.sleep(pause)
        cache[key] = None
        return None

    time.sleep(pause)
    formatted = format_address(data.get("address") or {}, data.get("display_name") or "")
    cache[key] = formatted or None
    return cache[key]


def save_non(rows: list[dict[str, Any]]) -> None:
    DATA_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = DATA_PATH.with_suffix(".csv")
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
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows: list[dict[str, Any]] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    targets = [r for r in rows if is_nyc_row(r)]
    print(f"Reverse-geocoding {len(targets)} NON New York locations…")

    cache = load_cache()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    filled = 0
    for i, row in enumerate(targets, start=1):
        lat = float(row["latitude"])
        lng = float(row["longitude"])
        street = reverse_geocode(lat, lng, session, cache)
        if street:
            row["address"] = street
            filled += 1
        if i % 10 == 0 or i == len(targets):
            save_cache(cache)
            print(f"  {i}/{len(targets)} processed ({filled} filled)")

    save_cache(cache)
    save_non(rows)
    print(f"Updated addresses for {filled}/{len(targets)} NYC NON locations")
    print(f"Wrote {DATA_PATH}")


if __name__ == "__main__":
    main()
