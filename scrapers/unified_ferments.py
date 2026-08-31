"""Scrape Unified Ferments FIND-US venue names and resolve street addresses."""

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

COMPETITOR = "Unified Ferments"
SOURCE_URL = "https://unifiedferments.com/FIND-US"
USER_AGENT = "comp-drink/0.1 (+local competitor stockist research)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"

# Page typos / names that need a stronger search form
NAME_ALIASES: dict[str, str] = {
    "NUATILUS": "Nautilus",
    "A BATHHOUSE": "Bathhouse New York",
    "BAR AT MONIKER GENERAL": "Moniker General",
    "HOLY BASIL DTLA": "Holy Basil Los Angeles",
    "FORMAGGIO SOUTH END": "Formaggio Kitchen 268 Shawmut Ave Boston",
    "PEOPLE’S WINE": "People's Wine New York",
    "PEOPLE'S WINE": "People's Wine New York",
    "CHARLIE’S NAPA": "Charlie's Napa",
    "JON AND VINNY’S": "Jon and Vinny's",
    "NICO’S BOTTLE SHOP": "Nico's Bottle Shop",
    "BACCO’S WINE AND CHEESE": "Baccos Wine and Cheese Boston",
    "BACCO'S WINE AND CHEESE": "Baccos Wine and Cheese Boston",
}

# Hardcoded when OSM/Photon miss a known stockist (name -> street address to geocode)
MANUAL_ADDRESSES: dict[str, str] = {
    "FORMAGGIO SOUTH END": "268 Shawmut Ave, Boston, MA 02118, USA",
    "BACCO’S WINE AND CHEESE": "31 Saint James Avenue, Boston, MA, USA",
    "BACCO'S WINE AND CHEESE": "31 Saint James Avenue, Boston, MA, USA",
}

CITY_HINTS: dict[str, list[str]] = {
    "NY": ["Brooklyn, NY, USA", "Manhattan, New York, NY, USA", "New York, NY, USA"],
    "CA": [
        "Los Angeles, CA, USA",
        "San Francisco, CA, USA",
        "Oakland, CA, USA",
        "Berkeley, CA, USA",
        "Napa, CA, USA",
    ],
    "NJ": ["Jersey City, NJ, USA", "Hoboken, NJ, USA", "New Jersey, USA"],
    "DC": ["Washington, DC, USA"],
    "MA": ["Boston, MA, USA", "Cambridge, MA, USA", "Massachusetts, USA"],
    "NC": ["Asheville, NC, USA", "Durham, NC, USA", "Charlotte, NC, USA", "North Carolina, USA"],
    "SC": ["Charleston, SC, USA", "Greenville, SC, USA", "South Carolina, USA"],
    "VT": ["Burlington, VT, USA", "Vermont, USA"],
}


# Page region headers -> search context + expected US state code
REGION_META: dict[str, dict[str, str]] = {
    "NEW YORK": {"query": "New York, NY, USA", "state": "NY", "region": "New York"},
    "NEW YORK (CONT.)": {"query": "New York, NY, USA", "state": "NY", "region": "New York"},
    "CALIFORNIA": {"query": "California, USA", "state": "CA", "region": "California"},
    "NEW JERSEY": {"query": "New Jersey, USA", "state": "NJ", "region": "New Jersey"},
    "DC": {"query": "Washington, DC, USA", "state": "DC", "region": "Washington DC"},
    "MASS": {"query": "Massachusetts, USA", "state": "MA", "region": "Massachusetts"},
    "NORTH CAROLINA": {"query": "North Carolina, USA", "state": "NC", "region": "North Carolina"},
    "SOUTH CAROLINA": {"query": "South Carolina, USA", "state": "SC", "region": "South Carolina"},
    "VERMONT": {"query": "Vermont, USA", "state": "VT", "region": "Vermont"},
}

SKIP_LINES = {
    "FIND US",
    "FIND US — UNIFIED FERMENTS",
    "(THIS IS AN INCOMPLETE LIST)",
    "LINEUP",
    "ORDER",
    "ABOUT",
    "F.A.Q.",
    "WHOLESALE",
    "PRESS",
    "~SUBSCRIBE TO MAILING LIST~",
}


def _norm_name(name: str) -> str:
    return (
        name.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u02bc", "'")
        .replace("\u2032", "'")
        .strip()
    )


def _alias_or_manual(mapping: dict[str, str], name: str) -> str | None:
    if name in mapping:
        return mapping[name]
    n = _norm_name(name)
    if n in mapping:
        return mapping[n]
    for key, value in mapping.items():
        if _norm_name(key) == n:
            return value
    return None


def fetch_html(timeout: float = 60.0) -> str:
    response = requests.get(
        SOURCE_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def _clean_line(text: str) -> str:
    # Drop decorative symbol characters from Squarespace webfonts.
    text = re.sub(r"[\ue000-\uf8ff\ufe0e\ufe0f]", "", text)
    return " ".join(text.split()).strip()


def parse_venues(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [_clean_line(t) for t in soup.get_text("\n").splitlines()]
    lines = [ln for ln in lines if ln]

    venues: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    started = False

    for raw in lines:
        upper = raw.upper()
        if upper in SKIP_LINES or upper.startswith(""):
            continue
        if upper == "FIND US":
            started = True
            continue
        if not started and upper not in REGION_META:
            continue
        started = True

        if upper in REGION_META:
            current = REGION_META[upper]
            continue
        if current is None:
            continue
        # Footer / nav leftovers
        if len(raw) <= 1:
            continue

        venues.append(
            {
                "name": raw,
                "region": current["region"],
                "state": current["state"],
                "query_area": current["query"],
            }
        )
    return venues


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _state_ok(result: dict[str, Any], expected_state: str) -> bool:
    addr = result.get("address") or {}
    state = (addr.get("state") or "").strip()
    code = (addr.get("ISO3166-2-lvl4") or addr.get("state_code") or "").strip().upper()
    expected = expected_state.upper()
    if code.endswith(f"-{expected}") or code == expected:
        return True
    aliases = {
        "NY": {"new york"},
        "CA": {"california"},
        "NJ": {"new jersey"},
        "DC": {"district of columbia", "washington"},
        "MA": {"massachusetts"},
        "NC": {"north carolina"},
        "SC": {"south carolina"},
        "VT": {"vermont"},
    }
    if state.lower() in aliases.get(expected, set()):
        return True
    # DC sometimes comes back as city-only
    if expected == "DC" and (addr.get("city") or "").lower() in {"washington", "washington, d.c."}:
        return True
    return False


def _photon_state_ok(props: dict[str, Any], expected_state: str) -> bool:
    expected = expected_state.upper()
    state = (props.get("state") or "").strip()
    country = (props.get("countrycode") or props.get("country") or "").strip().lower()
    if country and country not in {"us", "usa", "united states"}:
        return False
    # Photon often returns the USPS code (e.g. "MA") rather than the full name.
    if state.upper() == expected:
        return True
    aliases = {
        "NY": {"new york"},
        "CA": {"california"},
        "NJ": {"new jersey"},
        "DC": {"district of columbia", "washington, d.c.", "washington"},
        "MA": {"massachusetts"},
        "NC": {"north carolina"},
        "SC": {"south carolina"},
        "VT": {"vermont"},
    }
    if state.lower() in aliases.get(expected, set()):
        return True
    if expected == "DC" and (props.get("city") or "").lower() in {
        "washington",
        "washington, d.c.",
    }:
        return True
    return False


def _format_photon_address(props: dict[str, Any]) -> str:
    parts = [
        props.get("housenumber"),
        props.get("street"),
        props.get("city") or props.get("town") or props.get("village") or props.get("district"),
        props.get("state"),
        props.get("postcode"),
        props.get("country"),
    ]
    return ", ".join(str(p) for p in parts if p)


def resolve_place(
    name: str,
    query_area: str,
    expected_state: str,
    session: requests.Session,
    cache: dict[str, Any],
    pause: float = 1.05,
    force: bool = False,
) -> dict[str, Any] | None:
    cache_key = f"uf::{name.lower()}::{expected_state.lower()}"
    if not force and cache_key in cache:
        return cache[cache_key]

    search_name = _alias_or_manual(NAME_ALIASES, name) or name
    areas = [query_area] + [h for h in CITY_HINTS.get(expected_state.upper(), []) if h != query_area]
    queries: list[str] = []
    manual = _alias_or_manual(MANUAL_ADDRESSES, name)
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

    hit: dict[str, Any] | None = None
    # On retry of a failed lookup, skip Nominatim (already exhausted) and use Photon.
    use_nominatim = not force

    if use_nominatim:
        for q in queries[:8]:
            try:
                res = session.get(
                    NOMINATIM_URL,
                    params={
                        "q": q,
                        "format": "jsonv2",
                        "addressdetails": 1,
                        "limit": 5,
                        "countrycodes": "us",
                    },
                    timeout=40,
                )
                res.raise_for_status()
                results = res.json() or []
            except requests.RequestException:
                time.sleep(pause)
                continue

            time.sleep(pause)
            for result in results:
                if not _state_ok(result, expected_state):
                    continue
                try:
                    lat = float(result["lat"])
                    lng = float(result["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                hit = {
                    "latitude": lat,
                    "longitude": lng,
                    "address": result.get("display_name") or "",
                    "osm_type": result.get("type") or "",
                    "query_used": q,
                    "provider": "nominatim",
                }
                break
            if hit:
                break

    if not hit:
        for q in queries[:12]:
            try:
                res = session.get(PHOTON_URL, params={"q": q, "limit": 5}, timeout=30)
                res.raise_for_status()
                features = res.json().get("features") or []
            except requests.RequestException:
                time.sleep(0.35)
                continue

            time.sleep(0.35)
            for feature in features:
                props = feature.get("properties") or {}
                if not _photon_state_ok(props, expected_state):
                    continue
                coords = (feature.get("geometry") or {}).get("coordinates") or []
                if len(coords) < 2:
                    continue
                lng, lat = float(coords[0]), float(coords[1])
                address = _format_photon_address(props) or props.get("name") or q
                hit = {
                    "latitude": lat,
                    "longitude": lng,
                    "address": address,
                    "osm_type": props.get("osm_value") or props.get("type") or "",
                    "query_used": q,
                    "provider": "photon",
                }
                break
            if hit:
                break

    cache[cache_key] = hit
    return hit


def enrich(venues: list[dict[str, str]], cache_path: Path, retry_unresolved: bool = True) -> list[dict[str, Any]]:
    cache = _load_cache(cache_path)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    for i, venue in enumerate(venues, start=1):
        cache_key = f"uf::{venue['name'].lower()}::{venue['state'].lower()}"
        force = bool(retry_unresolved and cache.get(cache_key) is None)
        resolved = resolve_place(
            venue["name"],
            venue["query_area"],
            venue["state"],
            session,
            cache,
            force=force,
        )
        row = {
            "competitor": COMPETITOR,
            "source_id": "",
            "name": venue["name"],
            "address": (resolved or {}).get("address") or f"{venue['name']}, {venue['region']}",
            "suburb": venue["region"],
            "region": "US",
            "state": venue["state"],
            "venue_type": "",
            "latitude": (resolved or {}).get("latitude"),
            "longitude": (resolved or {}).get("longitude"),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
            "geocode_status": "resolved" if resolved else "unresolved",
        }
        rows.append(row)
        if i % 10 == 0 or i == len(venues):
            _save_cache(cache_path, cache)
            resolved_n = sum(1 for r in rows if r["geocode_status"] == "resolved")
            print(f"  resolved {resolved_n}/{i} (processed {i}/{len(venues)})")

    _save_cache(cache_path, cache)
    return rows


def save(rows: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "unified_ferments_locations.json"
    csv_path = out_dir / "unified_ferments_locations.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    fieldnames = [
        "competitor",
        "source_id",
        "name",
        "address",
        "suburb",
        "region",
        "state",
        "venue_type",
        "latitude",
        "longitude",
        "source_url",
        "scraped_at",
        "geocode_status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    venues = parse_venues(fetch_html())
    print(f"Parsed {len(venues)} Unified Ferments venues")
    rows = enrich(venues, root / "data" / "uf_geocode_cache.json")
    json_path, csv_path = save(rows, root / "data")
    resolved = sum(1 for r in rows if r["geocode_status"] == "resolved")
    print(f"Resolved addresses for {resolved}/{len(rows)}")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    unresolved = [r["name"] for r in rows if r["geocode_status"] != "resolved"]
    if unresolved:
        print("Unresolved:", ", ".join(unresolved[:20]), ("…" if len(unresolved) > 20 else ""))


if __name__ == "__main__":
    main()
