"""Shared venue-name geocoding: Nominatim + Photon with geographic and type guards."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"

# viewbox: min_lon, max_lat, max_lon, min_lat (Nominatim x1,y1,x2,y2)
NYC_FIVE_BOROUGHS_VIEWBOX = (-74.26, 40.92, -73.70, 40.49)
NY_STATE_VIEWBOX = (-79.76, 45.02, -71.85, 40.48)

TERRITORY_VIEWBOXES: dict[str, tuple[float, float, float, float]] = {
    "NY": NY_STATE_VIEWBOX,
    "CA": (-124.48, 42.01, -114.13, 32.53),
    "MA": (-73.51, 42.89, -69.86, 41.24),
    "NJ": (-75.56, 41.36, -73.89, 38.93),
    "DC": (-77.12, 38.99, -76.91, 38.79),
    "NC": (-84.32, 36.59, -75.46, 33.84),
    "SC": (-83.35, 35.22, -78.54, 32.03),
    "VT": (-73.44, 45.02, -71.46, 42.73),
}

NY_BOROUGHS = frozenset(
    {"brooklyn", "queens", "manhattan", "bronx", "staten island", "the bronx"}
)

COUNTY_TO_BOROUGH = {
    "kings county": "Brooklyn",
    "queens county": "Queens",
    "new york county": "Manhattan",
    "richmond county": "Staten Island",
    "bronx county": "Bronx",
}

# Approximate borough boxes (min_lat, max_lat, min_lon, max_lon) for lat/lng fallback
NY_BOROUGH_BOXES: dict[str, tuple[float, float, float, float]] = {
    "Manhattan": (40.70, 40.88, -74.02, -73.91),
    "Brooklyn": (40.57, 40.74, -74.04, -73.83),
    "Queens": (40.54, 40.80, -73.96, -73.70),
    "Bronx": (40.79, 40.92, -73.93, -73.75),
    "Staten Island": (40.49, 40.65, -74.26, -74.05),
}

REJECT_OSM_CLASSES = frozenset({"boundary", "natural", "landuse", "highway"})
REJECT_OSM_TYPES = frozenset(
    {
        "island",
        "islet",
        "archipelago",
        "playground",
        "administrative",
        "locality",
        "hamlet",
        "neighbourhood",
        "suburb",
        "residential",
        "yes",
        "house",
        "apartments",
    }
)
ACCEPT_OSM_CLASSES = frozenset({"amenity", "shop", "tourism", "craft", "commercial", "office"})

PHOTON_BUSINESS_TYPES = frozenset(
    {
        "restaurant",
        "bar",
        "pub",
        "cafe",
        "wine_bar",
        "nightclub",
        "shop",
        "supermarket",
        "deli",
        "hotel",
        "brewery",
        "marketplace",
        "fast_food",
        "biergarten",
        "food",
        "wine",
        "grocery",
        "convenience",
        "bakery",
        "greengrocer",
        "alcohol",
        "bar_and_grill",
        "ice_cream",
        "food_court",
        "wholesale",
        "department_store",
        "mall",
        "hostel",
        "motel",
        "guest_house",
        "liquor",
        "wine_shop",
        "beverages",
        "cheese",
        "farm",
        "caterer",
        "coffee_shop",
        "tea",
        "distillery",
        "winery",
        "tavern",
        "lounge",
    }
)

MIN_CONFIDENCE = 0.55
MIN_CONFIDENCE_SHORT_NAME = 0.82
PHOTON_REVIEW_CONFIDENCE = 0.80
PHOTON_REVIEW_CONFIDENCE_AMBIGUOUS = 0.90

# Tokens that are common English words — venue names composed only of these need higher confidence.
COMMON_DICTIONARY_WORDS = frozenset(
    {
        "a", "an", "the", "as", "is", "at", "bar", "gem", "elf", "jewel", "flower",
        "little", "white", "tiger", "moon", "fly", "saga", "neighborhood", "goods",
        "nada", "open", "market", "sesame", "sorrel", "june", "daughter", "sisters",
        "wild", "tonchin", "neighbor", "extra", "pearl", "street", "cherry", "top",
        "green", "blue", "red", "black", "gold", "silver", "king", "queen", "sun",
        "star", "lake", "river", "hill", "park", "house", "room", "place", "corner",
        "north", "south", "east", "west", "new", "old", "big", "one", "two", "ten",
    }
)

REVIEW_LOG_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "geocode_manual_review.jsonl"


@dataclass
class GeocodeHit:
    latitude: float
    longitude: float
    address: str
    suburb: str
    confidence: float
    provider: str
    query_used: str
    osm_class: str = ""
    osm_type: str = ""
    needs_review: bool = False


def _norm_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def name_confidence(venue_name: str, result_name: str) -> float:
    a = _norm_token(venue_name)
    b = _norm_token(result_name)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    a_tokens = a.split()
    b_tokens = b.split()
    if len(a_tokens) == 1 and len(b_tokens) > 1 and len(a_tokens[0]) <= 5:
        if a_tokens[0] not in b_tokens:
            return ratio * 0.4
        if b_tokens[-1] == a_tokens[0] and len(b_tokens) > 1:
            return ratio * 0.55
    return ratio


def _confidence_threshold(venue_name: str) -> float:
    token = _norm_token(venue_name)
    if len(token.replace(" ", "")) <= 5:
        return MIN_CONFIDENCE_SHORT_NAME
    return MIN_CONFIDENCE


def is_ambiguous_venue_name(venue_name: str) -> bool:
    tokens = [t for t in _norm_token(venue_name).split() if t]
    if len(tokens) <= 2:
        return True
    return bool(tokens) and all(t in COMMON_DICTIONARY_WORDS for t in tokens)


def review_confidence_threshold(venue_name: str) -> float:
    if is_ambiguous_venue_name(venue_name):
        return PHOTON_REVIEW_CONFIDENCE_AMBIGUOUS
    return PHOTON_REVIEW_CONFIDENCE


def in_viewbox(lat: float, lng: float, viewbox: tuple[float, float, float, float]) -> bool:
    min_lon, max_lat, max_lon, min_lat = viewbox
    return min_lat <= lat <= max_lat and min_lon <= lng <= max_lon


def nominatim_viewbox_params(viewbox: tuple[float, float, float, float]) -> dict[str, str]:
    min_lon, max_lat, max_lon, min_lat = viewbox
    return {
        "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
        "bounded": "1",
    }


def is_business_osm(class_: str, type_: str) -> bool:
    c = (class_ or "").lower()
    t = (type_ or "").lower()
    if c in REJECT_OSM_CLASSES or t in REJECT_OSM_TYPES:
        return False
    if c == "leisure":
        return False
    if c == "place":
        return False
    if c in ACCEPT_OSM_CLASSES:
        return True
    if c == "building" and t not in REJECT_OSM_TYPES:
        return True
    return False


def is_business_photon(osm_value: str, type_: str) -> bool:
    v = (osm_value or type_ or "").lower()
    return v in PHOTON_BUSINESS_TYPES


def ny_borough_from_zip(postcode: str) -> str:
    z = (postcode or "").strip()[:5]
    if len(z) != 5 or not z.isdigit():
        return ""
    if z.startswith(("100", "101", "102")):
        return "Manhattan"
    if z.startswith("112"):
        return "Brooklyn"
    if z.startswith(("111", "113", "114", "116")):
        return "Queens"
    if z.startswith("104"):
        return "Bronx"
    if z.startswith("103"):
        return "Staten Island"
    return ""


def ny_borough_from_coords(lat: float, lng: float) -> str:
    for borough, (min_lat, max_lat, min_lon, max_lon) in NY_BOROUGH_BOXES.items():
        if min_lat <= lat <= max_lat and min_lon <= lng <= max_lon:
            return borough
    return ""


def _borough_from_address(addr: dict[str, Any]) -> str:
    borough = (addr.get("borough") or "").strip()
    if borough:
        return borough
    county = (addr.get("county") or "").strip().lower()
    for key, name in COUNTY_TO_BOROUGH.items():
        if key in county:
            return name
    city = (addr.get("city") or addr.get("town") or addr.get("village") or "").strip()
    if city.lower() in NY_BOROUGHS:
        return "Bronx" if city.lower() == "the bronx" else city.title()
    return ""


def _ny_suburb(
    postcode: str,
    lat: float | None,
    lng: float | None,
    addr: dict[str, Any],
) -> str:
    suburb = ny_borough_from_zip(postcode)
    if suburb:
        return suburb
    if lat is not None and lng is not None:
        suburb = ny_borough_from_coords(lat, lng)
        if suburb:
            return suburb
    borough = _borough_from_address(addr)
    if borough:
        return borough
    city = (addr.get("city") or addr.get("town") or addr.get("village") or "").strip()
    if city and not _is_street_like(city) and "county" not in city.lower():
        if not city.lower().startswith("town of"):
            return city
    return ""


def _is_street_like(label: str) -> bool:
    if not label:
        return False
    if re.match(r"^\d", label):
        return True
    street_words = {"street", "st", "avenue", "ave", "road", "rd", "boulevard", "blvd", "drive", "dr"}
    tokens = _norm_token(label).split()
    return any(t in street_words for t in tokens)


def _state_abbrev(state: str) -> str:
    s = (state or "").strip()
    if not s:
        return ""
    if len(s) == 2:
        return s.upper()
    aliases = {
        "new york": "NY",
        "california": "CA",
        "massachusetts": "MA",
        "new jersey": "NJ",
        "north carolina": "NC",
        "south carolina": "SC",
        "vermont": "VT",
        "district of columbia": "DC",
    }
    return aliases.get(s.lower(), s)


def normalize_from_nominatim(
    addr: dict[str, Any],
    state_hint: str,
    lat: float | None = None,
    lng: float | None = None,
) -> tuple[str, str]:
    house = (addr.get("house_number") or "").strip()
    road = (addr.get("road") or addr.get("pedestrian") or addr.get("footway") or "").strip()
    street = " ".join(p for p in (house, road) if p).strip()

    state = _state_abbrev(addr.get("state") or state_hint)
    postcode = (addr.get("postcode") or "").strip()

    if state == "NY":
        suburb = _ny_suburb(postcode, lat, lng, addr)
    else:
        city = (addr.get("city") or addr.get("town") or addr.get("village") or "").strip()
        suburb = city if city and not _is_street_like(city) and "county" not in city.lower() else ""

    if suburb.lower().startswith("town of"):
        suburb = ""

    parts = [p for p in (street, suburb, state, postcode) if p]
    return ", ".join(parts), suburb


def normalize_from_photon(
    props: dict[str, Any],
    state_hint: str,
    lat: float | None = None,
    lng: float | None = None,
) -> tuple[str, str]:
    house = (props.get("housenumber") or "").strip()
    street_name = (props.get("street") or "").strip()
    street = " ".join(p for p in (house, street_name) if p).strip()

    state = _state_abbrev(props.get("state") or state_hint)
    postcode = (props.get("postcode") or "").strip()

    if state == "NY":
        addr_stub = {
            "county": props.get("county") or "",
            "city": props.get("city") or "",
            "town": props.get("town") or "",
        }
        suburb = _ny_suburb(postcode, lat, lng, addr_stub)
    else:
        city = (props.get("city") or props.get("town") or "").strip()
        suburb = city if city and not _is_street_like(city) else ""

    parts = [p for p in (street, suburb, state, postcode) if p]
    return ", ".join(parts), suburb


def log_manual_review(
    venue_name: str,
    expected_state: str,
    reason: str,
    query: str = "",
    candidate: str = "",
    log_path: Path = REVIEW_LOG_DEFAULT,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "venue": venue_name,
        "state": expected_state,
        "reason": reason,
        "query": query,
        "candidate": candidate,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("geocode review: %s — %s", venue_name, reason)


def evaluate_nominatim_street(
    result: dict[str, Any],
    expected_state: str,
    viewbox: tuple[float, float, float, float] | None,
) -> GeocodeHit | None:
    try:
        lat = float(result["lat"])
        lng = float(result["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    if viewbox and not in_viewbox(lat, lng, viewbox):
        return None

    addr = result.get("address") or {}
    if not (addr.get("road") or addr.get("pedestrian") or addr.get("house_number")):
        return None

    address, suburb = normalize_from_nominatim(addr, expected_state, lat, lng)
    if not address:
        return None
    if expected_state == "NY" and not suburb:
        return None

    osm_class = (result.get("category") or result.get("class") or "").lower()
    osm_type = (result.get("type") or "").lower()

    return GeocodeHit(
        latitude=lat,
        longitude=lng,
        address=address,
        suburb=suburb,
        confidence=1.0,
        provider="nominatim",
        query_used="",
        osm_class=osm_class,
        osm_type=osm_type,
        needs_review=False,
    )


def evaluate_photon_street(
    feature: dict[str, Any],
    expected_state: str,
    viewbox: tuple[float, float, float, float] | None,
) -> GeocodeHit | None:
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    lng, lat = float(coords[0]), float(coords[1])

    if viewbox and not in_viewbox(lat, lng, viewbox):
        return None

    props = feature.get("properties") or {}
    if not (props.get("street") or props.get("housenumber")):
        return None

    address, suburb = normalize_from_photon(props, expected_state, lat, lng)
    if not address:
        return None
    if expected_state == "NY" and not suburb:
        return None

    return GeocodeHit(
        latitude=lat,
        longitude=lng,
        address=address,
        suburb=suburb,
        confidence=1.0,
        provider="photon",
        query_used="",
        osm_type=(props.get("osm_value") or props.get("type") or "").lower(),
        needs_review=False,
    )


def resolve_manual_street(
    street: str,
    expected_state: str,
    session: requests.Session,
    viewbox: tuple[float, float, float, float] | None,
    pause: float = 1.05,
) -> GeocodeHit | None:
    if viewbox:
        vb_params = nominatim_viewbox_params(viewbox)
        try:
            res = session.get(
                NOMINATIM_URL,
                params={
                    "q": street,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": 3,
                    "countrycodes": "us",
                    **vb_params,
                },
                timeout=40,
            )
            res.raise_for_status()
            for result in res.json() or []:
                hit = evaluate_nominatim_street(result, expected_state, viewbox)
                if hit:
                    hit.query_used = street
                    time.sleep(pause)
                    return hit
        except requests.RequestException:
            pass
        time.sleep(pause)

    try:
        res = session.get(PHOTON_URL, params={"q": street, "limit": 3}, timeout=30)
        res.raise_for_status()
        for feature in res.json().get("features") or []:
            hit = evaluate_photon_street(feature, expected_state, viewbox)
            if hit:
                hit.query_used = street
                time.sleep(0.35)
                return hit
    except requests.RequestException:
        pass
    time.sleep(0.35)
    return None


def evaluate_nominatim_result(
    venue_name: str,
    result: dict[str, Any],
    expected_state: str,
    viewbox: tuple[float, float, float, float] | None,
) -> GeocodeHit | None:
    try:
        lat = float(result["lat"])
        lng = float(result["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    if viewbox and not in_viewbox(lat, lng, viewbox):
        return None

    osm_class = (result.get("category") or result.get("class") or "").lower()
    osm_type = (result.get("type") or "").lower()
    if not is_business_osm(osm_class, osm_type):
        return None

    result_name = (result.get("name") or result.get("display_name", "").split(",")[0] or "").strip()
    confidence = name_confidence(venue_name, result_name)
    if confidence < _confidence_threshold(venue_name):
        return None

    addr = result.get("address") or {}
    address, suburb = normalize_from_nominatim(addr, expected_state, lat, lng)
    if not address or not suburb:
        return None

    return GeocodeHit(
        latitude=lat,
        longitude=lng,
        address=address,
        suburb=suburb,
        confidence=confidence,
        provider="nominatim",
        query_used="",
        osm_class=osm_class,
        osm_type=osm_type,
        needs_review=confidence < review_confidence_threshold(venue_name),
    )


def evaluate_photon_feature(
    venue_name: str,
    feature: dict[str, Any],
    expected_state: str,
    viewbox: tuple[float, float, float, float] | None,
) -> GeocodeHit | None:
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    lng, lat = float(coords[0]), float(coords[1])

    if viewbox and not in_viewbox(lat, lng, viewbox):
        return None

    props = feature.get("properties") or {}
    osm_value = (props.get("osm_value") or props.get("type") or "").lower()
    if not is_business_photon(osm_value, props.get("type") or ""):
        return None

    result_name = (props.get("name") or "").strip()
    confidence = name_confidence(venue_name, result_name)
    if confidence < _confidence_threshold(venue_name):
        return None

    address, suburb = normalize_from_photon(props, expected_state, lat, lng)
    if not address:
        return None
    if expected_state == "NY" and not suburb:
        return None

    return GeocodeHit(
        latitude=lat,
        longitude=lng,
        address=address,
        suburb=suburb,
        confidence=confidence,
        provider="photon",
        query_used="",
        osm_class="",
        osm_type=osm_value,
        needs_review=confidence < review_confidence_threshold(venue_name),
    )


def resolve_venue_name(
    venue_name: str,
    query_area: str,
    expected_state: str,
    session: requests.Session,
    queries: list[str],
    pause: float = 1.05,
    use_nominatim: bool = True,
    manual_street: str | None = None,
    review_log: Path = REVIEW_LOG_DEFAULT,
) -> GeocodeHit | None:
    viewbox = TERRITORY_VIEWBOXES.get(expected_state.upper())

    if manual_street:
        manual_hit = resolve_manual_street(
            manual_street, expected_state, session, viewbox, pause=pause
        )
        if manual_hit:
            manual_hit.query_used = manual_street
            return manual_hit

    best: GeocodeHit | None = None
    best_score = 0.0

    if use_nominatim and viewbox:
        vb_params = nominatim_viewbox_params(viewbox)
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
                        **vb_params,
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
                hit = evaluate_nominatim_result(venue_name, result, expected_state, viewbox)
                if hit and hit.confidence > best_score:
                    hit.query_used = q
                    best = hit
                    best_score = hit.confidence
            if best and best_score >= 0.75:
                return best

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
            hit = evaluate_photon_feature(venue_name, feature, expected_state, viewbox)
            if hit and hit.confidence > best_score:
                hit.query_used = q
                best = hit
                best_score = hit.confidence
        if best and best_score >= 0.75:
            return best

    if best:
        return best

    log_manual_review(
        venue_name,
        expected_state,
        "no confident match",
        query=queries[0] if queries else "",
        log_path=review_log,
    )
    return None
