"""Live probe: run venue_geocode against named test cases with provider tracing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

# Allow running as script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.unified_ferments import (
    CITY_HINTS,
    MANUAL_ADDRESSES,
    NAME_ALIASES,
    USER_AGENT,
    _alias_or_manual,
)
from scrapers.venue_geocode import (
    GeocodeHit,
    is_business_photon,
    ny_borough_from_zip,
    resolve_venue_name,
)

QUERY_AREA = "New York, NY, USA"
STATE = "NY"

SHOULD_NULL = [
    "SAGA",
    "THE FLY",
    "WHITE TIGER",
    "LITTLE FLOWER",
    "AS IS",
    "WINONA'S",
    "CHERRY ON TOP",
    "RUFFIAN",
    "FORT DEFIANCE",
]

SHOULD_RESOLVE = [
    "ASKA",
    "ROBERTA'S",
    "COMPAGNIE DE VINS",
    "CAMPBELL CHEESE AND GROCERY",
    "CROWN SHY",
]


def build_queries(name: str) -> list[str]:
    search_name = _alias_or_manual(NAME_ALIASES, name) or name
    areas = [QUERY_AREA] + [h for h in CITY_HINTS.get(STATE, []) if h != QUERY_AREA]
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
    return queries


def hit_dict(hit: GeocodeHit | None) -> dict | None:
    if not hit:
        return None
    return {
        "provider": hit.provider,
        "confidence": hit.confidence,
        "needs_review": hit.needs_review,
        "address": hit.address,
        "suburb": hit.suburb,
        "latitude": hit.latitude,
        "longitude": hit.longitude,
        "osm_type": hit.osm_type,
        "query_used": hit.query_used,
    }


def probe_name(name: str, session: requests.Session) -> GeocodeHit | None:
    queries = build_queries(name)
    manual = _alias_or_manual(MANUAL_ADDRESSES, name)
    return resolve_venue_name(
        name,
        QUERY_AREA,
        STATE,
        session,
        queries,
        manual_street=manual,
        review_log=Path(__file__).resolve().parents[1] / "data" / "geocode_probe_review.jsonl",
    )


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    print("=" * 72)
    print("SHOULD RETURN NULL (wrong statewide matches blocked)")
    print("=" * 72)
    for name in SHOULD_NULL:
        hit = probe_name(name, session)
        print(f"\n--- {name} ---")
        print("result:", json.dumps(hit_dict(hit), indent=2))

    print("\n" + "=" * 72)
    print("SHOULD RESOLVE")
    print("=" * 72)
    for name in SHOULD_RESOLVE:
        hit = probe_name(name, session)
        print(f"\n--- {name} ---")
        print("result:", json.dumps(hit_dict(hit), indent=2))


if __name__ == "__main__":
    main()
