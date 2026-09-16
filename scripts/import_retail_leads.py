#!/usr/bin/env python3
"""Import 'Retail_Wholesale Leads - Retail.csv' into data/retail_leads_locations.json.

- Explodes multi-brand 'Competitors Sold Here' cells into one row per competitor
  (matches the one-row-per-competitor model every other data/*_locations.json uses).
- Rows with no competitor tag are labeled 'Retail Prospects' so they render as a
  distinct marker/category instead of being folded into an existing brand.
- Geocodes every row that has a street address via the shared Nominatim helper.
- Status=Closed rows and rows with no address are kept in the record (so they still
  show up in the dashboard table) but are left without map-plottable status; the
  map build step in map_locations.py is responsible for excluding status="closed"
  from pins.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.venue_geocode import TERRITORY_VIEWBOXES, resolve_manual_street

SRC_CSV = ROOT / "data" / "Retail_Wholesale Leads - Retail.csv"
OUT_JSON = ROOT / "data" / "retail_leads_locations.json"

PROSPECT_LABEL = "Retail Prospects"
USER_AGENT = "astra-retail-leads-import/1.0 (maxabeplush@gmail.com)"


def normalize_state(city_region: str) -> str:
    if "," in city_region:
        return city_region.rsplit(",", 1)[-1].strip().upper()
    return ""


def load_rows() -> list[dict]:
    with SRC_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_records(raw_rows: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    records: list[dict] = []
    for i, row in enumerate(raw_rows):
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        address = (row.get("Address") or "").strip()
        city_region = (row.get("City / Region") or "").strip()
        status = "closed" if (row.get("Status") or "").strip().lower() == "closed" else "active"
        astra_prospect = (row.get("Astra Prospect?") or "").strip().lower() == "yes"
        competitors = [
            c.strip() for c in (row.get("Competitors Sold Here") or "").split(",") if c.strip()
        ]
        if not competitors:
            competitors = [PROSPECT_LABEL]

        base = {
            "source_id": f"retail-leads-{i}",
            "name": name,
            "address": address,
            "suburb": city_region.split(",")[0].strip() if city_region else "",
            "region": "US",
            "state": normalize_state(city_region),
            "venue_type": (row.get("Type") or "").strip(),
            "latitude": None,
            "longitude": None,
            "source_url": "internal: Retail_Wholesale Leads - Retail.csv",
            "scraped_at": now,
            "geocode_status": "pending" if address else "no_address",
            "contact": (row.get("Contact Page") or "").strip(),
            "instagram": (row.get("Instagram") or "").strip(),
            "website": (row.get("Website") or "").strip(),
            "phone": (row.get("Phone") or "").strip(),
            "email": (row.get("Email") or "").strip(),
            "verified": False,
            "needs_review": bool((row.get("Needs Review") or "").strip()),
            "status": status,
            "astra_prospect": astra_prospect,
            "competitors_sold_here": competitors,
            "notes": (row.get("Notes / Warm Intros") or "").strip(),
            "last_contacted": (row.get("Last Contacted") or "").strip(),
            "open_times": (row.get("Open Times") or "").strip(),
        }
        for comp in competitors:
            rec = dict(base)
            rec["competitor"] = comp
            records.append(rec)
    return records


def geocode(records: list[dict]) -> None:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    by_address: dict[str, str] = {}
    for r in records:
        if r["address"] and r["geocode_status"] == "pending":
            by_address[r["address"]] = r["state"]

    print(f"Geocoding {len(by_address)} unique addresses...")
    cache: dict[str, object] = {}
    for idx, (addr, state) in enumerate(sorted(by_address.items()), 1):
        viewbox = TERRITORY_VIEWBOXES.get(state)
        hit = resolve_manual_street(addr, state, session, viewbox, pause=1.05)
        if hit is None and viewbox is not None:
            hit = resolve_manual_street(addr, state, session, None, pause=1.05)
        cache[addr] = hit
        print(f"  [{idx}/{len(by_address)}] {addr[:65]:65s} -> {'OK' if hit else 'MISS'}")

    for r in records:
        if r["address"] and r["geocode_status"] == "pending":
            hit = cache.get(r["address"])
            if hit:
                r["latitude"] = hit.latitude
                r["longitude"] = hit.longitude
                r["geocode_status"] = "resolved"
                if hit.suburb and not r["suburb"]:
                    r["suburb"] = hit.suburb
            else:
                r["geocode_status"] = "failed"
                r["needs_review"] = True


def main() -> None:
    raw_rows = load_rows()
    records = build_records(raw_rows)
    print(f"Parsed {len(raw_rows)} CSV rows -> {len(records)} exploded location records")

    geocode(records)

    resolved = sum(1 for r in records if r["geocode_status"] == "resolved")
    failed = sum(1 for r in records if r["geocode_status"] == "failed")
    no_addr = sum(1 for r in records if r["geocode_status"] == "no_address")
    closed = sum(1 for r in records if r["status"] == "closed")
    print(
        f"resolved={resolved} failed={failed} no_address={no_addr} "
        f"closed(excluded from map pins)={closed}"
    )

    OUT_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
