"""Load Savoure stockist research and geocode addresses."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

COMPETITOR = "Savoure"
USER_AGENT = "comp-drink/0.1 (+local competitor stockist research)"
PHOTON_URL = "https://photon.komoot.io/api/"

RAW_LOCATIONS: list[dict[str, str]] = [
    {
        "name": "Foragers Market",
        "venue_type": "market",
        "contact": "+1 718-801-8400",
        "website": "",
        "hours": "",
        "address": "56 Adams St, Brooklyn, NY 11201",
        "notes": "Listed as foragers DUMBO. Google: Foragers Market.",
    },
    {
        "name": "LifeThyme Natural Market",
        "venue_type": "natural market",
        "contact": "+1 212-420-1600",
        "website": "",
        "hours": "Mon-Fri 7:30am-9:30pm; Sat 8am-9:30pm; Sun 8am-9pm",
        "address": "410 6th Ave, New York, NY 10011",
        "notes": "Listed as life thyme market.",
    },
    {
        "name": "Talbott & Arding",
        "venue_type": "market",
        "contact": "+1 518-828-3558",
        "website": "",
        "hours": "Tue-Thu 10am-5pm; Fri-Sat 10am-6pm; Sun 10am-5pm; Mon closed",
        "address": "202 Allen St, Hudson, NY 12534",
        "notes": "Hudson, NY.",
    },
    {
        "name": "& Sons Buttery",
        "venue_type": "buttery",
        "contact": "+1 347-789-3422",
        "website": "",
        "hours": "",
        "address": "447 Rogers Ave, Brooklyn, NY 11225",
        "notes": "Listed as & sons. Recent Google reviews suggest it may have closed — verify before outreach.",
    },
    {
        "name": "Dimes Deli",
        "venue_type": "deli/market",
        "contact": "+1 212-240-9410",
        "website": "",
        "hours": "Mon-Fri 8am-8pm; Sat-Sun 9am-8pm",
        "address": "143 Division St, New York, NY 10002",
        "notes": "Same address as Dimes Market.",
    },
    {
        "name": "Depanneur",
        "venue_type": "market",
        "contact": "+1 718-989-1022",
        "website": "",
        "hours": "Mon-Fri 7:30am-7pm; Sat-Sun 8am-7pm",
        "address": "242 Wythe Ave, Brooklyn, NY 11249",
        "notes": "",
    },
    {
        "name": "Lea Brooklyn",
        "venue_type": "market",
        "contact": "+1 718-928-7100",
        "website": "",
        "hours": "",
        "address": "1022 Cortelyou Rd, Brooklyn, NY 11218",
        "notes": "",
    },
    {
        "name": "Park Slope Food Coop",
        "venue_type": "food coop",
        "contact": "+1 718-622-0560",
        "website": "",
        "hours": "Mon-Sat 8am-9pm; Sun 8am-8pm",
        "address": "782 Union St, Brooklyn, NY 11215",
        "notes": "Member-only co-op — buying goes through receiving/purchasing committee.",
    },
    {
        "name": "R&D Foods",
        "venue_type": "specialty foods",
        "contact": "+1 347-915-1196",
        "website": "",
        "hours": "",
        "address": "602 Vanderbilt Ave, Brooklyn, NY 11238",
        "notes": "",
    },
    {
        "name": "Zabar's",
        "venue_type": "specialty grocery",
        "contact": "+1 212-787-2000",
        "website": "",
        "hours": "Mon-Sat 8am-7:30pm; Sun 9am-6pm",
        "address": "2245 Broadway, New York, NY 10024",
        "notes": "",
    },
    {
        "name": "Public Records",
        "venue_type": "bar/venue",
        "contact": "",
        "website": "",
        "hours": "Thu 6-10pm; Fri 6pm-4am; Sat 10am-4am; Sun 10am-9pm; Mon-Wed closed",
        "address": "233 Butler St, Brooklyn, NY 11217",
        "notes": "",
    },
    {
        "name": "Tabula Rasa Bar",
        "venue_type": "bar",
        "contact": "+1 213-290-6309",
        "website": "https://tabularasabar.com",
        "hours": "",
        "address": "5125 Hollywood Blvd, Los Angeles, CA 90027",
        "notes": "",
    },
    {
        "name": "Botanica Restaurant and Market",
        "venue_type": "restaurant and market",
        "contact": "+1 323-522-6106",
        "website": "https://botanicarestaurant.com",
        "hours": "",
        "address": "1620 Silver Lake Blvd, Los Angeles, CA 90026",
        "notes": "",
    },
    {
        "name": "Sqirl Away",
        "venue_type": "to-go market",
        "contact": "+1 323-284-8147",
        "website": "https://sqirlla.com",
        "hours": "",
        "address": "720 N Virgil Ave, Los Angeles, CA 90029",
        "notes": "To-go market next door to Sqirl; stocks pantry goods, coffee, tea, beer and wine. sqirlla.com covers both.",
    },
]

# Skipped until address confirmed:
# Village Grocery & Refillery — no confident match
# (nearest: Maison Jar 566 Leonard St Brooklyn; A Sustainable Village 50 University Pl Manhattan)


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def geocode_address(
    address: str,
    session: requests.Session,
    cache: dict[str, Any],
    pause: float = 0.4,
) -> tuple[float | None, float | None]:
    key = address.strip().lower()
    if key in cache and cache[key]:
        hit = cache[key]
        return hit.get("lat"), hit.get("lng")

    try:
        res = session.get(PHOTON_URL, params={"q": address, "limit": 1}, timeout=30)
        res.raise_for_status()
        features = res.json().get("features") or []
    except requests.RequestException:
        time.sleep(pause)
        cache[key] = None
        return None, None

    time.sleep(pause)
    if not features:
        cache[key] = None
        return None, None

    coords = (features[0].get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        cache[key] = None
        return None, None

    lng, lat = float(coords[0]), float(coords[1])
    cache[key] = {"lat": lat, "lng": lng}
    return lat, lng


def _infer_city(address: str) -> str:
    upper = address.upper()
    if "LOS ANGELES" in upper:
        return "Los Angeles"
    if "BROOKLYN" in upper:
        return "Brooklyn"
    if "HUDSON" in upper:
        return "Hudson"
    if "NEW YORK" in upper:
        return "New York"
    return ""


def build_rows() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    cache_path = root / "data" / "savoure_geocode_cache.json"
    cache = _load_cache(cache_path)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    print(f"Geocoding {len(RAW_LOCATIONS)} Savoure locations…")
    for i, item in enumerate(RAW_LOCATIONS, start=1):
        lat, lng = geocode_address(item["address"], session, cache)
        state = "CA" if ", CA" in item["address"].upper() or "CALIFORNIA" in item["address"].upper() else "NY"
        rows.append(
            {
                "competitor": COMPETITOR,
                "source_id": "",
                "name": item["name"],
                "address": item["address"],
                "suburb": _infer_city(item["address"]),
                "region": "US",
                "state": state,
                "venue_type": item["venue_type"],
                "contact": item["contact"],
                "website": item["website"],
                "hours": item["hours"],
                "notes": item["notes"],
                "latitude": lat,
                "longitude": lng,
                "source_url": "friend research — Savoure",
                "scraped_at": scraped_at,
                "geocode_status": "resolved" if lat is not None else "unresolved",
            }
        )
        if i % 5 == 0 or i == len(RAW_LOCATIONS):
            _save_cache(cache_path, cache)
            print(f"  {i}/{len(RAW_LOCATIONS)}")

    _save_cache(cache_path, cache)
    return rows


def save(rows: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "savoure_locations.json"
    csv_path = out_dir / "savoure_locations.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "competitor",
        "source_id",
        "name",
        "address",
        "suburb",
        "region",
        "state",
        "venue_type",
        "contact",
        "website",
        "hours",
        "notes",
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
    rows = build_rows()
    json_path, csv_path = save(rows, root / "data")
    resolved = sum(1 for r in rows if r["geocode_status"] == "resolved")
    print(f"Saved {len(rows)} Savoure locations ({resolved} geocoded)")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    print("Skipped: Village Grocery & Refillery (no confident address)")


if __name__ == "__main__":
    main()
