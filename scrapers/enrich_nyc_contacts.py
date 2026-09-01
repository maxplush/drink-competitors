"""Enrich NYC location records with contact info from OpenStreetMap (Nominatim)."""

from __future__ import annotations

import json
import math
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests

from scrapers.contact_fields import row_phone_email, save_json_csv, split_contact

USER_AGENT = "comp-drink/0.1 (+nyc contact enrichment)"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DETAILS = "https://nominatim.openstreetmap.org/details"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CACHE_PATH = DATA_DIR / "nyc_contact_cache.json"

NYC_BBOX = (40.49, -74.26, 40.92, -73.70)  # min_lat, min_lng, max_lat, max_lng


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_nyc_bbox(lat: float | None, lng: float | None) -> bool:
    if lat is None or lng is None:
        return False
    min_lat, min_lng, max_lat, max_lng = NYC_BBOX
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def is_nyc_row(row: dict[str, Any]) -> bool:
    region = (row.get("region") or "").strip().upper()
    if region and region not in {"US", "USA", "UNITED STATES", ""}:
        return False

    state = (row.get("state") or "").upper()
    if state == "NY":
        return True
    addr = (row.get("address") or "").upper()
    city = (row.get("suburb") or "").upper()
    if ", NY" in addr or "BROOKLYN" in addr or "QUEENS" in addr or "BRONX" in addr:
        return True
    if city in {"NEW YORK", "BROOKLYN", "QUEENS", "BRONX", "MANHATTAN", "STATEN ISLAND"}:
        return True
    return in_nyc_bbox(row.get("latitude"), row.get("longitude"))


def name_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _needs_enrichment(row: dict[str, Any]) -> bool:
    phone, _email = row_phone_email(row)
    website = (row.get("website") or "").strip()
    return not phone or not website


def _cache_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            row.get("competitor") or "",
            row.get("name") or "",
            row.get("address") or "",
        ]
    ).lower()


def _extract_contact(extratags: dict[str, Any]) -> dict[str, str]:
    phone = (extratags.get("phone") or extratags.get("contact:phone") or "").strip()
    website = (extratags.get("website") or extratags.get("contact:website") or "").strip()
    email = (extratags.get("email") or extratags.get("contact:email") or "").strip()
    hours = (extratags.get("opening_hours") or "").strip()
    return {
        "phone": phone,
        "email": email,
        "website": website,
        "hours": hours,
    }


def _pick_hit(
    name: str,
    hits: list[dict[str, Any]],
    lat: float | None,
    lng: float | None,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for hit in hits:
        hit_name = hit.get("name") or hit.get("display_name", "").split(",")[0]
        score = name_score(name, hit_name)
        if lat is not None and lng is not None and hit.get("lat") and hit.get("lon"):
            dist = haversine_m(lat, lng, float(hit["lat"]), float(hit["lon"]))
            if dist > 400:
                score *= 0.5
            elif dist < 120:
                score += 0.15
        if score > best_score:
            best_score = score
            best = hit
    if best_score >= 0.55:
        return best
    return None


class NominatimClient:
    def __init__(self, pause: float = 1.1):
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def search(self, query: str) -> list[dict[str, Any]]:
        time.sleep(self.pause)
        try:
            res = self.session.get(
                NOMINATIM_SEARCH,
                params={"q": query, "format": "jsonv2", "extratags": 1, "limit": 5},
                timeout=40,
            )
            res.raise_for_status()
            return res.json() or []
        except requests.RequestException:
            return []

    def details_extratags(self, osm_type: str, osm_id: int) -> dict[str, Any]:
        time.sleep(self.pause)
        try:
            res = self.session.get(
                NOMINATIM_DETAILS,
                params={
                    "osmtype": osm_type[0].upper(),
                    "osmid": osm_id,
                    "extratags": 1,
                    "format": "json",
                },
                timeout=40,
            )
            res.raise_for_status()
            data = res.json()
            return data.get("extratags") or {}
        except requests.RequestException:
            return {}


def _normalize_cached(found: dict[str, Any] | None) -> dict[str, str] | None:
    if not found:
        return None
    if found.get("contact") and not found.get("phone") and not found.get("email"):
        phone, email = split_contact(found.pop("contact"))
        if phone:
            found["phone"] = phone
        if email:
            found["email"] = email
    return found


def lookup_contact(
    row: dict[str, Any],
    client: NominatimClient,
    cache: dict[str, Any],
) -> dict[str, str] | None:
    key = _cache_key(row)
    if key in cache:
        return _normalize_cached(cache[key])

    name = (row.get("name") or "").strip()
    address = (row.get("address") or "").strip()
    city = (row.get("suburb") or "").strip()
    lat, lng = row.get("latitude"), row.get("longitude")

    queries = []
    if address:
        queries.append(f"{name}, {address}")
    if city:
        queries.append(f"{name}, {city}, NY")
    queries.append(f"{name}, New York, NY")

    hit: dict[str, Any] | None = None
    for q in queries:
        hits = client.search(q)
        hit = _pick_hit(name, hits, lat, lng)
        if hit:
            break

    if not hit:
        cache[key] = None
        return None

    extratags = hit.get("extratags") or {}
    if not extratags and hit.get("osm_type") and hit.get("osm_id"):
        extratags = client.details_extratags(hit["osm_type"], int(hit["osm_id"]))

    result = _extract_contact(extratags)
    if not any(result.values()):
        cache[key] = None
        return None

    result["contact_source"] = "openstreetmap"
    cache[key] = result
    return result


def apply_to_row(row: dict[str, Any], found: dict[str, str] | None) -> bool:
    if not found:
        return False
    changed = False
    for field in ("phone", "email", "website", "hours"):
        if found.get(field) and not (row.get(field) or "").strip():
            row[field] = found[field]
            changed = True
    if found.get("contact_source"):
        row["contact_source"] = found["contact_source"]
    return changed


def load_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def enrich_all(limit: int | None = None) -> dict[str, int]:
    cache = load_cache()
    client = NominatimClient()
    stats = {"targets": 0, "updated": 0, "found": 0, "skipped": 0}

    json_paths = sorted(DATA_DIR.glob("*_locations.json"))
    for path in json_paths:
        rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        file_changed = False

        for row in rows:
            if not is_nyc_row(row):
                continue
            if not _needs_enrichment(row):
                stats["skipped"] += 1
                continue
            if limit is not None and stats["targets"] >= limit:
                break

            stats["targets"] += 1
            found = lookup_contact(row, client, cache)
            if found:
                stats["found"] += 1
            if apply_to_row(row, found):
                stats["updated"] += 1
                file_changed = True

            if stats["targets"] % 10 == 0:
                save_cache(cache)
                print(f"  processed {stats['targets']} NYC rows ({stats['found']} with contact data)")

        if file_changed:
            save_json_csv(path, rows)
            print(f"Updated {path.name}")

        save_cache(cache)
        if limit is not None and stats["targets"] >= limit:
            break

    return stats


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Enrich NYC locations with OSM contact info")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process (for testing)")
    args = parser.parse_args()

    targets = [
        r
        for p in DATA_DIR.glob("*_locations.json")
        for r in json.loads(p.read_text(encoding="utf-8"))
        if is_nyc_row(r) and _needs_enrichment(r)
    ]
    print(f"NYC locations needing contact enrichment: {len(targets)}")
    stats = enrich_all(limit=args.limit)
    print(
        f"Done — processed {stats['targets']}, found {stats['found']}, "
        f"updated {stats['updated']}, already complete {stats['skipped']}"
    )


if __name__ == "__main__":
    main()
