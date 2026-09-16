#!/usr/bin/env python3
"""Retire stale NYC rows that data/retail_leads_locations.json now supersedes.

The retail-leads sheet is the up-to-date source of truth for NYC venues, so any
venue that appears there is dropped from the older per-competitor scrape files
(unified_ferments, non, savoure, villbrygg) IF the old row is (a) inside the NYC
five-borough box and (b) has the same competitor brand and normalized name as a
retail-leads row. Removed rows are backed up before the source files are rewritten.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.contact_fields import save_json_csv
from scrapers.venue_geocode import NYC_FIVE_BOROUGHS_VIEWBOX, in_viewbox

DATA_DIR = ROOT / "data"
RETAIL_LEADS_JSON = DATA_DIR / "retail_leads_locations.json"
SUPERSEDE_TARGETS = [
    "unified_ferments_locations.json",
    "non_locations.json",
    "savoure_locations.json",
    "villbrygg_locations.json",
]


def normalize_name(name: str) -> str:
    s = (name or "").upper()
    for ch in ("'", "’", "`", "´"):
        s = s.replace(ch, "'")
    s = re.sub(r"[^A-Z0-9' &]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    retail_leads = json.loads(RETAIL_LEADS_JSON.read_text(encoding="utf-8"))
    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in retail_leads:
        comp = r.get("competitor") or ""
        name = normalize_name(r.get("name") or "")
        if comp and name:
            by_key.setdefault((comp, name), []).append(r)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = DATA_DIR / "migrations" / f"retail_leads_supersede_{stamp}"

    retail_leads_changed = False
    total_removed = 0
    for fname in SUPERSEDE_TARGETS:
        path = DATA_DIR / fname
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        keep: list[dict] = []
        removed: list[dict] = []
        for row in rows:
            comp = row.get("competitor") or ""
            name = normalize_name(row.get("name") or "")
            lat, lng = row.get("latitude"), row.get("longitude")
            in_nyc = (
                lat is not None
                and lng is not None
                and in_viewbox(float(lat), float(lng), NYC_FIVE_BOROUGHS_VIEWBOX)
            )
            key = (comp, name)
            if in_nyc and key in by_key:
                # A hand-verified old row is more trustworthy than a fresh geocode —
                # carry its verified flag + coordinates onto the record(s) that
                # replace it instead of silently downgrading a confirmed address.
                if row.get("verified"):
                    for target in by_key[key]:
                        target["verified"] = True
                        if lat is not None and lng is not None:
                            target["latitude"] = lat
                            target["longitude"] = lng
                            target["geocode_status"] = "resolved"
                        retail_leads_changed = True
                removed.append(row)
            else:
                keep.append(row)

        print(f"{fname}: {len(rows)} rows -> removing {len(removed)}, keeping {len(keep)}")
        for row in removed:
            flag = " (verified, propagated)" if row.get("verified") else ""
            print(f"    - [{row.get('competitor')}] {row.get('name')} | {row.get('address')}{flag}")

        if removed:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / fname).write_text(
                json.dumps(removed, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            save_json_csv(path, keep)
            total_removed += len(removed)

    if retail_leads_changed:
        save_json_csv(RETAIL_LEADS_JSON, retail_leads)
        print(f"Updated {RETAIL_LEADS_JSON} with propagated verified flags/coordinates")

    if total_removed:
        print(f"\nBacked up removed rows to {backup_dir}")
    print(f"Total rows superseded: {total_removed}")


if __name__ == "__main__":
    main()
