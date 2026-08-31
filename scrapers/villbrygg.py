"""Scrape Villbrygg stockists from https://villbrygg.com/en/shops (exclude Online)."""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

COMPETITOR = "Villbrygg"
SHOPS_URL = "https://villbrygg.com/en/shops"
USER_AGENT = "comp-drink/0.1 (+local competitor stockist research)"
PHOTON_URL = "https://photon.komoot.io/api/"


def fetch_html(timeout: float = 60.0) -> str:
    response = requests.get(
        SHOPS_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def _is_online(city: str, category: str) -> bool:
    return city.strip().lower() == "online" or category.strip().lower() == "online"


def parse_locations(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    for country in soup.select(".country"):
        country_el = country.select_one(".country-name")
        country_name = country_el.get_text(" ", strip=True) if country_el else ""

        for city in country.select(".city"):
            city_el = city.select_one(".city-name")
            city_name = city_el.get_text(" ", strip=True) if city_el else ""

            # Online cities list shops directly under .location-list (no category).
            direct_locs = city.select(":scope > .city-content > .location-list > li.location")
            if direct_locs and city_name.strip().lower() == "online":
                continue

            categories = city.select(".locations-category")
            if not categories and direct_locs:
                # Non-online city with flat list (unlikely, but keep defensive).
                categories = [city]

            for cat in categories:
                cat_el = cat.select_one(".category-name")
                category = cat_el.get_text(" ", strip=True) if cat_el else ""
                if _is_online(city_name, category):
                    continue

                for loc in cat.select("li.location"):
                    name_el = loc.select_one(".location-name")
                    name = name_el.get_text(" ", strip=True) if name_el else ""
                    addr_el = loc.select_one(".block-content")
                    address = addr_el.get_text(" ", strip=True) if addr_el else ""
                    if not name or not address:
                        continue
                    # Skip pure webshop notes that slipped through.
                    if address.strip().lower() in {"home delivery", "online"}:
                        continue

                    link = ""
                    a = loc.select_one("a[href]")
                    if a and a.get("href"):
                        link = a["href"].strip()

                    rows.append(
                        {
                            "competitor": COMPETITOR,
                            "source_id": "",
                            "name": name,
                            "address": address,
                            "suburb": city_name,
                            "region": country_name,
                            "venue_type": category,
                            "latitude": None,
                            "longitude": None,
                            "website": link,
                            "source_url": SHOPS_URL,
                            "scraped_at": scraped_at,
                        }
                    )
    return rows


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def geocode_address(
    address: str,
    session: requests.Session,
    cache: dict[str, Any],
    pause: float = 0.35,
) -> tuple[float | None, float | None]:
    key = address.strip().lower()
    if key in cache:
        hit = cache[key]
        if hit and hit.get("lat") is not None and hit.get("lng") is not None:
            return float(hit["lat"]), float(hit["lng"])
        return None, None

    params = {"q": address, "limit": 1}
    # Bias toward Norway when address ends with Norway.
    if re.search(r"\bnorway\b", address, re.I):
        params["osm_tag"] = "place"

    try:
        res = session.get(PHOTON_URL, params={"q": address, "limit": 1}, timeout=30)
        res.raise_for_status()
        features = res.json().get("features") or []
    except requests.RequestException:
        cache[key] = None
        time.sleep(pause)
        return None, None

    if not features:
        cache[key] = None
        time.sleep(pause)
        return None, None

    coords = features[0].get("geometry", {}).get("coordinates") or []
    if len(coords) < 2:
        cache[key] = None
        time.sleep(pause)
        return None, None

    lng, lat = float(coords[0]), float(coords[1])
    cache[key] = {"lat": lat, "lng": lng}
    time.sleep(pause)
    return lat, lng


def geocode_rows(rows: list[dict[str, Any]], cache_path: Path) -> list[dict[str, Any]]:
    cache = _load_cache(cache_path)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for i, row in enumerate(rows, start=1):
        lat, lng = geocode_address(row["address"], session, cache)
        row["latitude"] = lat
        row["longitude"] = lng
        if i % 25 == 0 or i == len(rows):
            _save_cache(cache_path, cache)
            print(f"  geocoded {i}/{len(rows)}")

    _save_cache(cache_path, cache)
    return rows


def save(rows: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "villbrygg_locations.json"
    csv_path = out_dir / "villbrygg_locations.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

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
        "website",
        "source_url",
        "scraped_at",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path


def scrape(geocode: bool = True) -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    rows = parse_locations(fetch_html())
    if geocode:
        print(f"Geocoding {len(rows)} Villbrygg addresses…")
        rows = geocode_rows(rows, root / "data" / "geocode_cache.json")
    return rows


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    rows = scrape(geocode=True)
    mapped = sum(1 for r in rows if r.get("latitude") is not None)
    json_path, csv_path = save(rows, root / "data")
    print(f"Scraped {len(rows)} Villbrygg locations (Online excluded)")
    print(f"Geocoded {mapped}/{len(rows)}")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
