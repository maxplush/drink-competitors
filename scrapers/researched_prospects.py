"""Load researched prospect locations (friend research) and geocode addresses."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from scrapers.contact_fields import split_contact

COMPETITOR = "Researched Prospect Locations"
USER_AGENT = "comp-drink/0.1 (+local competitor stockist research)"
PHOTON_URL = "https://photon.komoot.io/api/"

# Columns: name, venue_type, contact, website, hours, address, notes
RAW_PROSPECTS: list[dict[str, str]] = [
    {
        "name": "The Maze",
        "venue_type": "alcohol-free members club",
        "contact": "",
        "website": "https://www.themazenyc.com/",
        "hours": "Tue-Wed 11am-10pm; Thu-Fri 11am-11pm; Sat 10am-11pm; Mon & Sun closed",
        "address": "43 W 24th St, New York, NY 10010",
        "notes": "",
    },
    {
        "name": "Happier Grocery",
        "venue_type": "specialty grocery store",
        "contact": "+1 212-837-8015",
        "website": "",
        "hours": "Daily 8am-9pm",
        "address": "365 Canal St, Ground Floor, New York, NY 10013",
        "notes": "",
    },
    {
        "name": "Dimes Market",
        "venue_type": "market",
        "contact": "@dimestimes | +1 646-870-5113",
        "website": "",
        "hours": "Mon-Fri 8am-8pm; Sat-Sun 9am-8pm",
        "address": "143 Division St, New York, NY 10002",
        "notes": "",
    },
    {
        "name": "Gem",
        "venue_type": "specialty grocery store",
        "contact": "info@gemhomenyc.com",
        "website": "",
        "hours": "9:00am - 6:00pm",
        "address": "181 Mott St, New York, NY 10012",
        "notes": "",
    },
    {
        "name": "Silence Please",
        "venue_type": "tea house and listening room",
        "contact": "",
        "website": "https://silenceplease.com/",
        "hours": "Daily 10am-8pm",
        "address": "132 Bowery, Fl 2, New York, NY 10013",
        "notes": "",
    },
    {
        "name": "Pocketbook Market",
        "venue_type": "market",
        "contact": "",
        "website": "",
        "hours": "",
        "address": "549 Washington St, Hudson, NY 12534",
        "notes": "Hudson, NY (not NYC)",
    },
    {
        "name": "Entre Nous",
        "venue_type": "wine bar",
        "contact": "+1 347-294-4186",
        "website": "",
        "hours": "Tue-Thu 5-11pm; Fri 5pm-12am; Sat 3pm-12am; Sun 3-11pm; Mon closed",
        "address": "39 Clifton Pl, Brooklyn, NY 11238",
        "notes": "",
    },
    {
        "name": "Rhodora",
        "venue_type": "wine bar",
        "contact": "",
        "website": "",
        "hours": "Tue-Thu 5-11pm; Fri 4pm-12am; Sat 1pm-12am; Sun 1-11pm; Mon closed",
        "address": "197 Adelphi St, Brooklyn, NY 11205",
        "notes": "",
    },
    {
        "name": "Prima",
        "venue_type": "cafe/wine bar",
        "contact": "+1 718-789-7890",
        "website": "",
        "hours": "Mon-Tue 8am-5pm; Wed-Sat 8am-10pm; Sun 8am-8pm",
        "address": "147 Greene Ave, Brooklyn, NY 11238",
        "notes": "",
    },
    {
        "name": "Place Des Fetes",
        "venue_type": "restaurant",
        "contact": "+1 718-857-0101",
        "website": "",
        "hours": "Sun-Thu 5:30-10pm; Fri-Sat 5:30-10:30pm",
        "address": "212 Greene Ave, Brooklyn, NY 11238",
        "notes": "",
    },
    {
        "name": "Anais",
        "venue_type": "wine bar",
        "contact": "+1 718-513-9176",
        "website": "",
        "hours": "Mon-Thu 8am-11pm; Fri 8am-12am; Sat 9am-12am; Sun 9am-11pm",
        "address": "196 Bergen St #2, Brooklyn, NY 11217",
        "notes": "",
    },
    {
        "name": "L'Apero by 4F",
        "venue_type": "wine bar",
        "contact": "+1 929-337-6022",
        "website": "",
        "hours": "Wed-Sat 6-10pm; Sun-Tue closed",
        "address": "115 Montague St, Brooklyn, NY 11201",
        "notes": "",
    },
    {
        "name": "Clover Club",
        "venue_type": "bar",
        "contact": "+1 718-855-7939",
        "website": "",
        "hours": "Mon-Thu 4pm-12am; Fri 4pm-2am; Sat 12pm-2am; Sun 12pm-12am",
        "address": "210 Smith St, Brooklyn, NY 11201",
        "notes": "",
    },
    {
        "name": "Doris",
        "venue_type": "bar",
        "contact": "+1 347-240-3350",
        "website": "",
        "hours": "Mon-Wed 5pm-1am; Thu 5pm-2am; Fri 5pm-3am; Sat 4pm-3am; Sun 4pm-1am",
        "address": "1088 Fulton St, Brooklyn, NY 11238",
        "notes": "",
    },
    {
        "name": "Ornithology Jazz Club",
        "venue_type": "bar",
        "contact": "+1 917-231-4766",
        "website": "",
        "hours": "Daily 6pm-2am",
        "address": "6 Suydam St, Brooklyn, NY 11221",
        "notes": "",
    },
    {
        "name": "The Coyote Club",
        "venue_type": "bar",
        "contact": "",
        "website": "",
        "hours": "Mon-Fri 4pm-4am; Sat-Sun 2pm-4am",
        "address": "417 Throop Ave, Brooklyn, NY 11221",
        "notes": "",
    },
    {
        "name": "LunAtico",
        "venue_type": "bar",
        "contact": "+1 718-513-0339",
        "website": "",
        "hours": "Daily 5pm-1am",
        "address": "486 Halsey St, Brooklyn, NY 11233",
        "notes": "",
    },
    {
        "name": "With Others",
        "venue_type": "wine bar",
        "contact": "+1 929-389-0807",
        "website": "",
        "hours": "Wed-Thu 5pm-12am; Fri 4pm-12am; Sat 2pm-12am; Sun 2-11pm; Mon-Tue closed",
        "address": "340 Bedford Ave, Brooklyn, NY 11249",
        "notes": "",
    },
    {
        "name": "Sauced",
        "venue_type": "wine bar",
        "contact": "+1 929-492-4758",
        "website": "",
        "hours": "Mon-Wed 5pm-12am; Thu 5pm-1am; Fri 5pm-2am; Sat 2pm-2am; Sun 2pm-12am",
        "address": "331 Bedford Ave, Brooklyn, NY 11211",
        "notes": "",
    },
    {
        "name": "Bar Laika",
        "venue_type": "wine bar",
        "contact": "+1 347-529-4321",
        "website": "",
        "hours": "Mon-Thu 6pm-12am; Fri-Sat 6pm-1am; Sun closed",
        "address": "224 Greene Ave, Brooklyn, NY 11238",
        "notes": "",
    },
    {
        "name": "TIME AGAIN",
        "venue_type": "bar",
        "contact": "",
        "website": "",
        "hours": "Mon-Wed 5pm-2am; Thu-Sun 2pm-2am",
        "address": "105 Canal St, New York, NY 10002",
        "notes": "",
    },
    {
        "name": "Mr Fongs",
        "venue_type": "bar",
        "contact": "+1 646-964-4540",
        "website": "",
        "hours": "Mon 4pm-12am; Tue-Thu 4pm-2am; Fri-Sat 4pm-4am; Sun 4pm-12am",
        "address": "40 Market St, New York, NY 10002",
        "notes": "",
    },
    {
        "name": "Kingston Hall",
        "venue_type": "bar",
        "contact": "",
        "website": "",
        "hours": "Mon-Thu 4pm-2am; Fri-Sat 4pm-4am; Sun 3pm-12am",
        "address": "149 2nd Ave, New York, NY 10003",
        "notes": "",
    },
    {
        "name": "Ray's",
        "venue_type": "bar",
        "contact": "",
        "website": "",
        "hours": "Mon-Thu 5pm-2am; Fri 3pm-2am; Sat-Sun 1pm-2am",
        "address": "177 Chrystie St, New York, NY 10002",
        "notes": "",
    },
    {
        "name": "Frog Wine Bar",
        "venue_type": "wine bar",
        "contact": "+1 347-294-4197",
        "website": "",
        "hours": "Mon-Fri 2-8pm; Sat-Sun 12-8pm",
        "address": "389 Henry St, Brooklyn, NY 11201",
        "notes": "",
    },
    {
        "name": "George and Jack's Tap Room",
        "venue_type": "pub",
        "contact": "",
        "website": "",
        "hours": "Daily 12pm-4am",
        "address": "103 Berry St, Brooklyn, NY 11211",
        "notes": "",
    },
    {
        "name": "Lilia",
        "venue_type": "restaurant",
        "contact": "+1 718-576-3095",
        "website": "",
        "hours": "Mon-Thu 5-9:30pm; Fri-Sun 4-9:30pm",
        "address": "567 Union Ave, Brooklyn, NY 11211",
        "notes": "",
    },
    {
        "name": "Misipasta",
        "venue_type": "cafe",
        "contact": "+1 347-844-6474",
        "website": "",
        "hours": "Mon-Thu 12-10pm; Fri-Sun 11am-10pm",
        "address": "46 Grand St, Brooklyn, NY 11249",
        "notes": "",
    },
    {
        "name": "Misi",
        "venue_type": "restaurant",
        "contact": "+1 347-566-3262",
        "website": "",
        "hours": "Mon-Thu 5-9:30pm; Fri-Sun 11:30am-2:30pm & 4:30-9:30pm",
        "address": "329 Kent Ave, Brooklyn, NY 11249",
        "notes": "",
    },
    {
        "name": "Nowadays",
        "venue_type": "bar/club",
        "contact": "",
        "website": "",
        "hours": "Thu 5pm-12am; Fri 5pm-6am; Sat 2pm-12am; Sun to 10pm; Mon-Wed closed",
        "address": "56-06 Cooper Ave, Glendale, NY 11385",
        "notes": "",
    },
    {
        "name": "Carmelo's",
        "venue_type": "bar",
        "contact": "",
        "website": "",
        "hours": "Daily 4pm-4am",
        "address": "1544 Dekalb Ave, Brooklyn, NY 11237",
        "notes": "",
    },
    {
        "name": "Le Dive",
        "venue_type": "wine bar",
        "contact": "",
        "website": "",
        "hours": "Mon 3pm-12am; Tue-Wed 3pm-1am; Thu-Fri 3pm-2am; Sat 12pm-2am; Sun 12pm-12am",
        "address": "37 Canal St, New York, NY 10002",
        "notes": "",
    },
    {
        "name": "Banter Bar",
        "venue_type": "pub",
        "contact": "+1 347-457-6621",
        "website": "",
        "hours": "Mon-Tue 2pm-12am; Wed-Fri 2pm-2am; Sat-Sun 12pm-2am",
        "address": "132 Havemeyer St, Brooklyn, NY 11211",
        "notes": "",
    },
    {
        "name": "Radegast Hall",
        "venue_type": "pub",
        "contact": "+1 718-963-3973",
        "website": "",
        "hours": "Mon-Thu 12pm-1am; Fri 12pm-3am; Sat 11am-3am; Sun 11am-1am",
        "address": "113 N 3rd St, Brooklyn, NY 11249",
        "notes": "",
    },
    {
        "name": "Lovers of Today",
        "venue_type": "bar",
        "contact": "",
        "website": "",
        "hours": "Daily 5pm-4am",
        "address": "132 1/2 E 7th St, New York, NY 10009",
        "notes": "",
    },
    {
        "name": "Joyface",
        "venue_type": "bar",
        "contact": "",
        "website": "",
        "hours": "Tue-Wed 7pm-2am; Thu-Sat 7pm-3am; Sun 7pm-2am; Mon closed",
        "address": "104 Loisaida Ave, New York, NY 10009",
        "notes": "",
    },
    {
        "name": "Twins Lounge",
        "venue_type": "bar/lounge",
        "contact": "",
        "website": "",
        "hours": "Mon-Thu 4pm-3am; Fri-Sat 3pm-4am; Sun 4pm-3am",
        "address": "732 Manhattan Ave, Brooklyn, NY 11222",
        "notes": "",
    },
    {
        "name": "The Spaniard",
        "venue_type": "bar",
        "contact": "+1 212-918-1986",
        "website": "",
        "hours": "Mon-Wed 12pm-2am; Thu-Fri 12pm-4am; Sat 11am-4am; Sun 11am-2am",
        "address": "190 W 4th St, New York, NY 10014",
        "notes": "",
    },
    {
        "name": "Air HQ",
        "venue_type": "software company with pop-up space",
        "contact": "212-920-1426",
        "website": "",
        "hours": "Mon-Fri 9am-5pm",
        "address": "3 Howard St, New York, NY 10013",
        "notes": "",
    },
    {
        "name": "Verlaine",
        "venue_type": "cocktail bar",
        "contact": "Max is cousins with the owner Gary",
        "website": "https://www.verlainenyc.com/",
        "hours": "Tue-Wed 5pm-12am; Thu 5pm-1am; Fri-Sat 5pm-2am; Sun 5-11pm; Mon closed",
        "address": "110 Rivington St, New York, NY 10002",
        "notes": "",
    },
    {
        "name": "Soft Bar",
        "venue_type": "non-alc dedicated bar + cafe",
        "contact": "",
        "website": "",
        "hours": "Tue-Sat 8am-10pm; Sun 8am-7pm; Mon closed",
        "address": "200 Banker St, Brooklyn, NY 11222",
        "notes": "",
    },
    {
        "name": "AITA",
        "venue_type": "",
        "contact": "+1 718-576-3584",
        "website": "https://aitaclintonhill.com",
        "hours": "",
        "address": "132 Greene Ave, Brooklyn, NY 11238",
        "notes": "",
    },
    {
        "name": "Canyon Grocer by Kurt & Whey",
        "venue_type": "small grocer",
        "contact": "kurt@kurtandwhey.com | +1 805-444-8255",
        "website": "",
        "hours": "Mon-Tue 9:30am-4pm; Wed-Sun 9am-4pm",
        "address": "169 W Channel Rd, Santa Monica, CA 90402",
        "notes": "Santa Monica CA, not NYC. Email corrected from kurt@kurt@whey.com.",
    },
]


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


def _infer_city_region(address: str) -> tuple[str, str]:
    upper = address.upper()
    if "SANTA MONICA" in upper or ", CA" in upper:
        return "Santa Monica", "US"
    if "HUDSON, NY" in upper:
        return "Hudson", "US"
    if "BROOKLYN" in upper:
        return "Brooklyn", "US"
    if "GLENDALE" in upper:
        return "Glendale", "US"
    if "NEW YORK" in upper or ", NY" in upper:
        return "New York", "US"
    return "", "US"


def build_rows() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    cache_path = root / "data" / "prospects_geocode_cache.json"
    cache = _load_cache(cache_path)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    print(f"Geocoding {len(RAW_PROSPECTS)} researched prospect locations…")
    for i, item in enumerate(RAW_PROSPECTS, start=1):
        city, region = _infer_city_region(item["address"])
        lat, lng = geocode_address(item["address"], session, cache)
        phone, email = split_contact(item["contact"])
        rows.append(
            {
                "competitor": COMPETITOR,
                "source_id": "",
                "name": item["name"],
                "address": item["address"],
                "suburb": city,
                "region": region,
                "state": "CA" if ", CA" in item["address"].upper() else "NY",
                "venue_type": item["venue_type"],
                "phone": phone,
                "email": email,
                "website": item["website"],
                "hours": item["hours"],
                "notes": item["notes"],
                "latitude": lat,
                "longitude": lng,
                "source_url": "friend research",
                "scraped_at": scraped_at,
                "geocode_status": "resolved" if lat is not None else "unresolved",
            }
        )
        if i % 10 == 0 or i == len(RAW_PROSPECTS):
            _save_cache(cache_path, cache)
            print(f"  {i}/{len(RAW_PROSPECTS)}")

    _save_cache(cache_path, cache)
    return rows


def save(rows: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "researched_prospects_locations.json"
    csv_path = out_dir / "researched_prospects_locations.csv"
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
        "phone",
        "email",
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
    print(f"Saved {len(rows)} researched prospects ({resolved} geocoded)")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
