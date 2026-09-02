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

from scrapers.venue_geocode import GeocodeHit, normalize_from_photon, resolve_venue_name

COMPETITOR = "Unified Ferments"
SOURCE_URL = "https://unifiedferments.com/FIND-US"
USER_AGENT = "comp-drink/0.1 (+local competitor stockist research)"
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
    "ASKA": "47 S 5th St, Brooklyn, NY 11249, USA",
    "ATOMIX": "104 E 30th St, New York, NY 10016, USA",
    "DINNER PARTY BROOKLYN": "274 Hall St, Brooklyn, NY 11205, USA",
    "GETAWAY": "743 Riverside Dr, New York, NY 10031, USA",
    "MOONFLOWER": "201 W 11th St, New York, NY 10014, USA",
    "CHEESEPLATE BROOKLYN": "323 Court St, Brooklyn, NY 11231, USA",
    "LIL DEB'S OASIS": "747 Columbia St, Hudson, NY 12534, USA",
    "LIL DEB’S OASIS": "747 Columbia St, Hudson, NY 12534, USA",
    "KINDRED FARE": "512 Hamilton St, Geneva, NY 14456, USA",
    "AS IS": "734 10th Ave, New York, NY 10019, USA",
    "EXTRA EXTRA PIZZA": "549 W Utica St, Buffalo, NY 14213, USA",
    "PEARL STREET SUPPER CLUB": "147 Front St, Brooklyn, NY 11201, USA",
    "SAGA": "70 Pine St, 63rd Floor, New York, NY 10005, USA",
    "THE GETAWAY 151": "743 Riverside Dr, New York, NY 10031, USA",
    "The Getaway 151": "743 Riverside Dr, New York, NY 10031, USA",
}

# Permanently closed — drop from dataset even if still listed on FIND-US
REMOVE_NAMES = {
    "BOLERO",
    "ILIS",
    "MENA",
    "ANTO",
    "WHITE TIGER",
    "WHITE TIGER TAVERN",
    "WINONA'S",
    "WINONA’S",
}

# Post-scrape fixes: rename / replace address / expand one listing into multiple sites
# Each entry replaces the scraped row for that name.
LOCATION_OVERRIDES: dict[str, list[dict[str, str]]] = {
    "ASKA": [{"name": "ASKA", "address": "47 S 5th St, Brooklyn, NY 11249, USA"}],
    "ATOMIX": [{"name": "ATOMIX", "address": "104 E 30th St, New York, NY 10016, USA"}],
    "CHEESEPLATE BROOKLYN": [
        {"name": "Cheeseplate Brooklyn", "address": "323 Court St, Brooklyn, NY 11231, USA"},
        {"name": "Cheeseplate Brooklyn", "address": "400 7th Ave, Brooklyn, NY 11215, USA"},
    ],
    "DINNER PARTY BROOKLYN": [
        {"name": "Dinner Party", "address": "274 Hall St, Brooklyn, NY 11205, USA"},
    ],
    "GETAWAY": [
        {"name": "The Getaway 151", "address": "743 Riverside Dr, New York, NY 10031, USA"},
    ],
    "MOONFLOWER": [
        {"name": "MOONFLOWER", "address": "201 West 11th Street, Manhattan, NY 10014, USA"},
    ],
    "BONNIES": [
        {
            "name": "BONNIES",
            "address": "398 Manhattan Ave, Brooklyn, NY 11211, USA",
            "phone": "(914) 875-3709",
            "website": "https://bonniesbrooklyn.com",
        },
    ],
    "LOLO WINE BAR": [
        {
            "name": "LOLO WINE BAR",
            "address": "5140 Sunset Blvd, Los Angeles, CA 90027, USA",
            "phone": "(323) 665-5656",
            "website": "https://lolowinebar.com",
        },
    ],
    "AITA": [
        {
            "name": "AITA",
            "address": "132 Greene Ave, Brooklyn, NY 11238, USA",
            "phone": "(718) 576-3584",
            "website": "https://aitaclintonhill.com",
        },
    ],
    "ALISON": [
        {
            "name": "ALISON",
            "address": "1651 Lexington Ave, New York, NY 10029, USA",
            "phone": "(646) 876-1054",
            "website": "https://alisonny.com",
        },
    ],
    "SAGA": [
        {
            "name": "SAGA",
            "address": "70 Pine St, 63rd Floor, New York, NY 10005, USA",
            "phone": "(212) 339-3963",
        },
    ],
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


def _hit_to_cache_dict(hit: GeocodeHit) -> dict[str, Any]:
    return {
        "latitude": hit.latitude,
        "longitude": hit.longitude,
        "address": hit.address,
        "suburb": hit.suburb,
        "osm_type": hit.osm_type,
        "query_used": hit.query_used,
        "provider": hit.provider,
        "confidence": hit.confidence,
        "needs_review": hit.needs_review,
    }


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

    manual = _alias_or_manual(MANUAL_ADDRESSES, name)
    hit = resolve_venue_name(
        name,
        query_area,
        expected_state,
        session,
        queries,
        pause=pause,
        use_nominatim=not force,
        manual_street=manual,
    )
    result = _hit_to_cache_dict(hit) if hit else None
    cache[cache_key] = result
    return result


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
            "suburb": (resolved or {}).get("suburb") or venue["region"],
            "region": "US",
            "state": venue["state"],
            "venue_type": "",
            "latitude": (resolved or {}).get("latitude"),
            "longitude": (resolved or {}).get("longitude"),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
            "geocode_status": "resolved" if resolved else "unresolved",
            "verified": False,
            "needs_review": bool((resolved or {}).get("needs_review")),
        }
        rows.append(row)
        if i % 10 == 0 or i == len(venues):
            _save_cache(cache_path, cache)
            resolved_n = sum(1 for r in rows if r["geocode_status"] == "resolved")
            print(f"  resolved {resolved_n}/{i} (processed {i}/{len(venues)})")

    _save_cache(cache_path, cache)
    return rows


def geocode_street_address(
    address: str,
    session: requests.Session,
    cache: dict[str, Any],
    pause: float = 0.4,
) -> tuple[float | None, float | None, str]:
    """Geocode a known street address (Photon first). Returns lat, lng, display address."""
    key = f"addr::{address.strip().lower()}"
    if key in cache and cache[key]:
        hit = cache[key]
        return hit.get("latitude"), hit.get("longitude"), hit.get("address") or address

    try:
        res = session.get(PHOTON_URL, params={"q": address, "limit": 1}, timeout=30)
        res.raise_for_status()
        features = res.json().get("features") or []
    except requests.RequestException:
        time.sleep(pause)
        cache[key] = None
        return None, None, address

    time.sleep(pause)
    if not features:
        cache[key] = None
        return None, None, address

    feature = features[0]
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        cache[key] = None
        return None, None, address

    lng, lat = float(coords[0]), float(coords[1])
    props = feature.get("properties") or {}
    state_hint = (props.get("state") or "NY")[:2].upper() if props.get("state") else "NY"
    display, _ = normalize_from_photon(props, state_hint)
    if not display:
        display = address
    cache[key] = {"latitude": lat, "longitude": lng, "address": display}
    return lat, lng, display


def apply_corrections(rows: list[dict[str, Any]], cache_path: Path) -> list[dict[str, Any]]:
    """Remove closed venues and apply manual address/name overrides."""
    cache = _load_cache(cache_path)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    remove = {_norm_name(n).upper() for n in REMOVE_NAMES}
    overrides = {_norm_name(k).upper(): v for k, v in LOCATION_OVERRIDES.items()}

    out: list[dict[str, Any]] = []
    for row in rows:
        key = _norm_name(row.get("name") or "").upper()
        if key in remove:
            continue
        if row.get("verified"):
            out.append(row)
            continue
        if key not in overrides:
            out.append(row)
            continue

        template = {k: v for k, v in row.items()}
        for override in overrides[key]:
            new_row = dict(template)
            new_row["name"] = override.get("name") or row["name"]
            street = override["address"]
            lat, lng, display = geocode_street_address(street, session, cache)
            new_row["address"] = street if not display else street
            # Prefer the user-provided street text; keep geocoded coords.
            new_row["address"] = street
            new_row["latitude"] = lat
            new_row["longitude"] = lng
            new_row["geocode_status"] = "resolved" if lat is not None else "unresolved"
            if override.get("phone"):
                new_row["phone"] = override["phone"]
                new_row["contact"] = ""
            if override.get("website"):
                new_row["website"] = override["website"]
            out.append(new_row)

    _save_cache(cache_path, cache)
    return out


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
        "scraped_address",
        "suburb",
        "region",
        "state",
        "venue_type",
        "latitude",
        "longitude",
        "source_url",
        "scraped_at",
        "geocode_status",
        "verified",
        "needs_review",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    cache_path = root / "data" / "uf_geocode_cache.json"
    json_path = root / "data" / "unified_ferments_locations.json"

    from scrapers.location_merge import (
        apply_verified_and_review_flags,
        load_json_rows,
        merge_scraped_into_existing,
        uf_row_key,
    )

    existing = load_json_rows(json_path)
    venues = parse_venues(fetch_html())
    print(f"Parsed {len(venues)} Unified Ferments venues")
    scraped = enrich(venues, cache_path)
    scraped = apply_corrections(scraped, cache_path)
    rows = merge_scraped_into_existing(existing, scraped, uf_row_key)
    rows = apply_verified_and_review_flags(rows)
    json_path, csv_path = save(rows, root / "data")
    resolved = sum(1 for r in rows if r["geocode_status"] == "resolved")
    print(f"After corrections: {len(rows)} venues ({resolved} resolved)")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    unresolved = [r["name"] for r in rows if r["geocode_status"] != "resolved"]
    if unresolved:
        print("Unresolved:", ", ".join(unresolved[:20]), ("…" if len(unresolved) > 20 else ""))


if __name__ == "__main__":
    main()
