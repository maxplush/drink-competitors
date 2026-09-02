"""
One-off, idempotent, reversible migration for competitor location tables.

Apply verified address corrections, flag uncertain rows, delete bad ZIP rows,
merge duplicates, and fix known data errors in *_locations.json files.

Usage:
  python scripts/migrate_location_corrections.py          # apply (idempotent)
  python scripts/migrate_location_corrections.py --undo # restore pre-migration backups
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data"
MIGRATIONS_DIR = DATA_DIR / "migrations"
BACKUP_DIR = MIGRATIONS_DIR / "location_corrections_backup"
MANIFEST_PATH = MIGRATIONS_DIR / "location_corrections_manifest.json"

TARGET_FILES = [
    DATA_DIR / "unified_ferments_locations.json",
    DATA_DIR / "non_locations.json",
]

# Verified corrections: canonical key -> street line (match on competitor + name aliases).
VERIFIED_ADDRESSES: dict[str, str] = {
    "BLANCA": "261 Moore St, Brooklyn, NY 11206",
    "FORT DEFIANCE": "354 Van Brunt St, Brooklyn, NY 11231",
    "NARO": "610 5th Ave, New York, NY 10020",
    "RUFFIAN": "125 E 7th St, New York, NY 10009",
    "SAGA": "70 Pine St, New York, NY 10005",
    "WINONAS": "676 Flushing Ave, Brooklyn, NY 11206",
    "RHODORA": "197 Adelphi St, Brooklyn, NY 11205",
    "SOMM TIME": "254 Broome St, New York, NY 10002",
    "MISSION CHINESE": "45 Mott St, New York, NY 10013",
    "NICHE NICHE": "43 MacDougal St, New York, NY 10011",
    "HANA MAKGEOLLI": "201 Dupont St, Brooklyn, NY 11222",
    "FULGURANCES": "132 Franklin St, Brooklyn, NY 11222",
    "BATHHOUSE": "103 N 10th St, Brooklyn, NY 11249",
    "BAR MERIDIAN": "406 Prospect Pl, Brooklyn, NY 11238",
    "ODDLY ENOUGH": "397 Tompkins Ave, Brooklyn, NY 11216",
    "WEN WEN": "1025 Manhattan Ave, Brooklyn, NY 11222",
    "THE FLY": "549 Classon Ave, Brooklyn, NY 11238",
    "CHERRY ON TOP": "379 Suydam St, Brooklyn, NY 11237",
    "PEOPLES WINE": "115 Delancey St, New York, NY 10002",
    "AMAN NEW YORK": "730 5th Ave, New York, NY 10019",
    "CROWN SHY": "70 Pine St, New York, NY 10005",
    "SOHO GRAND HOTEL": "310 W Broadway, New York, NY 10013",
    "SMITH & MILLS": "71 N Moore St, New York, NY 10013",
    "BOTTLEROCKET": "5 W 19th St, New York, NY 10011",
    "EDITION HOTELS": "5 Madison Ave, New York, NY 10010",
    "CLAUDETTE": "24 Fifth Ave, New York, NY 10011",
    "GAGE & TOLLNER": "372 Fulton St, Brooklyn, NY 11201",
}

NAME_ALIASES: dict[str, list[str]] = {
    "BATHHOUSE": ["A BATHHOUSE"],
    "RHODORA": ["RHODORA WINE BAR"],
    "FULGURANCES": ["FULGURANCES LAUNDROMAT"],
    "WINONAS": ["WINONA'S", "WINONA’S"],
    "PEOPLES WINE": ["PEOPLE'S WINE", "PEOPLE’S WINE"],
    "BOTTLEROCKET": ["BOTTLEROCKET WINE & SPIRIT"],
    "THE GETAWAY 151": ["The Getaway 151"],
}

FLAG_REVIEW_NAMES = {
    "WHITE TIGER",
    "AS IS",
    "LITTLE FLOWER",
    "PEARL STREET SUPPER CLUB",
    "EXTRA EXTRA PIZZA",
    "THE GETAWAY 151",
    "MOONFLOWER",
    "KINDRED FARE",
}

CLOSED_OR_CHANGED_NAMES = {"FORT DEFIANCE", "SAINT JULIVERT"}

DELETE_BY_NAME_ZIP = {
    "CULINARY PURSUITS": "10000",
    "QUALITY BURGER": "10000",
}

MANUAL_CORRECTIONS: dict[str, dict[str, Any]] = {
    "CLAUDETTE": {
        "address": "24 Fifth Ave, New York, NY 10011, USA",
        "latitude": 40.73528,
        "longitude": -73.99647,
        "suburb": "New York",
        "state": "NY",
    },
    "NICHE NICHE": {
        "address": "43 MacDougal St, New York, NY 10011, USA",
        "latitude": 40.72842,
        "longitude": -74.00158,
        "suburb": "New York",
        "state": "NY",
    },
    "ODDLY ENOUGH": {
        "address": "397 Tompkins Ave, Brooklyn, NY 11216, USA",
        "latitude": 40.68312,
        "longitude": -73.94204,
        "suburb": "Brooklyn",
        "state": "NY",
    },
}

MERGE_PAIRS = [
    ("CHEESE PLATE BROOKLYN", "Cheese Plate Park Slope"),
    ("SANGS", "SAA"),
    ("CONVENE", "CONVENE #0"),
]

NYC_ZIP_RE = re.compile(r"\b(100|101|102|103|104|112|113|114)\d{2}\b")


def normalize_name(name: str) -> str:
    s = (name or "").upper()
    for ch in ("’", "`", "´"):
        s = s.replace(ch, "'")
    s = re.sub(r"[^A-Z0-9'& ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def build_name_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key, street in VERIFIED_ADDRESSES.items():
        names = {normalize_name(key)}
        for alias in NAME_ALIASES.get(key, []):
            names.add(normalize_name(alias))
        for n in names:
            lookup[n] = street
    return lookup


def field_count(row: dict[str, Any]) -> int:
    return sum(1 for v in row.values() if v is not None and str(v).strip() != "")


def merge_row_fields(keep: dict[str, Any], other: dict[str, Any]) -> None:
    for key, value in other.items():
        if value is None or str(value).strip() == "":
            continue
        if keep.get(key) is None or str(keep.get(key)).strip() == "":
            keep[key] = value


def parse_street_line(street_line: str) -> tuple[str, str, str, str]:
    """Return (address, suburb, state, postcode) from a verified street line."""
    m = re.match(
        r"^(.+?),\s*([^,]+),\s*([A-Z]{2})\s+(\d{5})\s*$",
        street_line.strip(),
    )
    if not m:
        return f"{street_line}, USA", "", "NY", ""
    street, suburb, state, zip_code = m.groups()
    address = f"{street}, {suburb}, {state} {zip_code}, USA"
    return address, suburb, state, zip_code


def addresses_match(current: str, target_line: str) -> bool:
    target_addr, _, _, target_zip = parse_street_line(target_line)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    if norm(current) == norm(target_addr):
        return True
    return bool(target_zip and target_zip in (current or ""))


def needs_county_town_review(row: dict[str, Any]) -> bool:
    """Flag bad NY geocodes (upstate County / Town of), not OSM County labels in NYC."""
    addr = row.get("address", "") or ""
    state = (row.get("state") or "").upper()
    if "Town of" in addr:
        return True
    if "County" in addr and state == "NY" and not NYC_ZIP_RE.search(addr):
        return True
    return False


def geocode_street(street_line: str, session: requests.Session) -> dict[str, Any] | None:
    from scrapers.venue_geocode import NY_STATE_VIEWBOX, resolve_manual_street

    _, _, state, _ = parse_street_line(street_line)
    expected_state = state or "NY"
    hit = resolve_manual_street(
        street_line,
        expected_state,
        session,
        NY_STATE_VIEWBOX if expected_state == "NY" else None,
        pause=1.05,
    )
    if not hit:
        return None
    return {
        "latitude": hit.latitude,
        "longitude": hit.longitude,
        "address": hit.address,
        "suburb": hit.suburb,
        "state": expected_state,
        "geocode_status": "resolved",
    }


def apply_verified_correction(
    row: dict[str, Any],
    street_line: str,
    session: requests.Session,
    summary: dict[str, list[str]],
) -> bool:
    if row.get("verified"):
        _, _, _, target_zip = parse_street_line(street_line)
        if target_zip and target_zip in (row.get("address") or ""):
            return False

    before = copy.deepcopy(row)
    norm = normalize_name(row.get("name", ""))
    manual = MANUAL_CORRECTIONS.get(norm)
    if manual:
        row.update(manual)
        geo = None
    else:
        geo = geocode_street(street_line, session)
        address, suburb, state, zip_code = parse_street_line(street_line)
        row["address"] = geo["address"] if geo else address
        row["suburb"] = geo.get("suburb") or suburb if geo else suburb
        row["state"] = geo.get("state") or state if geo else state
        if geo:
            row["latitude"] = geo["latitude"]
            row["longitude"] = geo["longitude"]
            row["geocode_status"] = geo["geocode_status"]
    row["needs_review"] = False
    row["verified"] = True
    if row.get("region") in ("", None):
        row["region"] = "US"

    label = f"{row.get('competitor')}|{row.get('name')}"
    summary["corrected"].append(f"{label}: {before.get('address')} -> {row['address']}")
    return True


def flag_needs_review(row: dict[str, Any], reason: str, summary: dict[str, list[str]]) -> bool:
    if row.get("needs_review"):
        return False
    row["needs_review"] = True
    label = f"{row.get('competitor')}|{row.get('name')}"
    summary["flagged"].append(f"{label}: {reason}")
    return True


def backup_files() -> list[str]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backed: list[str] = []
    for path in TARGET_FILES:
        if not path.exists():
            continue
        dest = BACKUP_DIR / path.name
        if not dest.exists():
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            backed.append(path.name)
    return backed


def undo() -> None:
    if not BACKUP_DIR.exists():
        print("No backups found — nothing to undo.")
        return
    restored = []
    for path in TARGET_FILES:
        backup = BACKUP_DIR / path.name
        if backup.exists():
            path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            restored.append(path.name)
            from scrapers.contact_fields import save_json_csv

            save_json_csv(path, json.loads(path.read_text(encoding="utf-8")))
    if MANIFEST_PATH.exists():
        MANIFEST_PATH.unlink()
    print(f"Restored {len(restored)} file(s) from backup: {', '.join(restored)}")


def find_row_by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    target = normalize_name(name)
    for row in rows:
        if normalize_name(row.get("name", "")) == target:
            return row
    return None


def apply_migrations(dry_run: bool = False) -> dict[str, list[str]]:
    name_lookup = build_name_lookup()
    flag_names = {normalize_name(n) for n in FLAG_REVIEW_NAMES}
    flag_names.update(normalize_name(a) for a in NAME_ALIASES.get("THE GETAWAY 151", []))
    closed_names = {normalize_name(n) for n in CLOSED_OR_CHANGED_NAMES}

    summary: dict[str, list[str]] = {
        "corrected": [],
        "flagged": [],
        "closed_or_changed": [],
        "deleted": [],
        "merged": [],
        "fixed": [],
    }

    session = requests.Session()
    session.headers.update({"User-Agent": "comp-drink/0.1 (+local competitor stockist research)"})

    file_rows: dict[Path, list[dict[str, Any]]] = {}
    for path in TARGET_FILES:
        if path.exists():
            file_rows[path] = json.loads(path.read_text(encoding="utf-8"))

    if not dry_run:
        backed = backup_files()
        if backed:
            print(f"Backed up: {', '.join(backed)}")

    # Verified corrections (match brand + name across all target files).
    for path, rows in file_rows.items():
        for row in rows:
            norm = normalize_name(row.get("name", ""))
            street_line = name_lookup.get(norm)
            if street_line:
                apply_verified_correction(row, street_line, session, summary)

    # Closed / changed status.
    for path, rows in file_rows.items():
        for row in rows:
            if normalize_name(row.get("name", "")) not in closed_names:
                continue
            if row.get("status") == "closed_or_changed":
                continue
            row["status"] = "closed_or_changed"
            label = f"{row.get('competitor')}|{row.get('name')}"
            summary["closed_or_changed"].append(label)

    # Flag named venues (no address guess).
    for path, rows in file_rows.items():
        for row in rows:
            if normalize_name(row.get("name", "")) in flag_names:
                flag_needs_review(row, "manual review list", summary)

    # Flag County / Town of on remaining bad NY geocodes.
    for path, rows in file_rows.items():
        for row in rows:
            if row.get("verified"):
                continue
            if needs_county_town_review(row):
                flag_needs_review(
                    row,
                    f"address contains County/Town of: {(row.get('address') or '')[:80]}",
                    summary,
                )

    # Deletes (NON bad ZIP 10000).
    non_path = DATA_DIR / "non_locations.json"
    if non_path in file_rows:
        rows = file_rows[non_path]
        kept: list[dict[str, Any]] = []
        for row in rows:
            norm = normalize_name(row.get("name", ""))
            zip_target = DELETE_BY_NAME_ZIP.get(norm)
            if zip_target and zip_target in (row.get("address") or ""):
                label = f"{row.get('competitor')}|{row.get('name')}"
                summary["deleted"].append(f"{label}: {row.get('address')}")
                continue
            kept.append(row)
        file_rows[non_path] = kept

    # Merges (NON only).
    if non_path in file_rows:
        rows = file_rows[non_path]
        for name_a, name_b in MERGE_PAIRS:
            row_a = find_row_by_name(rows, name_a)
            row_b = find_row_by_name(rows, name_b)
            if not row_a or not row_b:
                continue
            keep, drop = (row_a, row_b) if field_count(row_a) >= field_count(row_b) else (row_b, row_a)
            merge_row_fields(keep, drop)
            label = f"{keep.get('competitor')}|{keep.get('name')} <- {drop.get('name')}"
            summary["merged"].append(label)
            rows[:] = [r for r in rows if r is not drop]

    # Other fixes (NON).
    if non_path in file_rows:
        for row in file_rows[non_path]:
            norm = normalize_name(row.get("name", ""))
            if norm == "FOOD GARDEN MARKET":
                addr = row.get("address", "")
                if "11228" in addr:
                    row["address"] = addr.replace("11228", "11238")
                    summary["fixed"].append(
                        f"FOOD GARDEN MARKET ZIP 11228 -> 11238: {row['address']}"
                    )
            if norm == "DRYSPACE BEVERAGE":
                addr = row.get("address", "")
                if "New York, New York" in addr:
                    row["address"] = addr.replace(
                        "New York, New York", "Little Neck, Queens, NY"
                    )
                    row["suburb"] = "Little Neck"
                    summary["fixed"].append(f"DrySpace Beverage city -> Little Neck: {row['address']}")

    if not dry_run:
        from scrapers.contact_fields import save_json_csv

        for path, rows in file_rows.items():
            save_json_csv(path, rows)

        MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
        manifest = {
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "backup_dir": str(BACKUP_DIR),
            "summary": {k: len(v) for k, v in summary.items()},
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return summary


def print_summary(summary: dict[str, list[str]]) -> None:
    print("\n=== Location corrections migration summary ===")
    print(f"Corrected ({len(summary['corrected'])}):")
    for line in summary["corrected"]:
        print(f"  - {line}")
    print(f"Flagged needs_review ({len(summary['flagged'])}):")
    for line in summary["flagged"]:
        print(f"  - {line}")
    print(f"Closed or changed ({len(summary['closed_or_changed'])}):")
    for line in summary["closed_or_changed"]:
        print(f"  - {line}")
    print(f"Deleted ({len(summary['deleted'])}):")
    for line in summary["deleted"]:
        print(f"  - {line}")
    print(f"Merged ({len(summary['merged'])}):")
    for line in summary["merged"]:
        print(f"  - {line}")
    print(f"Other fixes ({len(summary['fixed'])}):")
    for line in summary["fixed"]:
        print(f"  - {line}")
    print(
        f"\nTotals: {len(summary['corrected'])} corrected, "
        f"{len(summary['flagged'])} flagged, "
        f"{len(summary['deleted'])} deleted, "
        f"{len(summary['merged'])} merged, "
        f"{len(summary['fixed'])} fixed, "
        f"{len(summary['closed_or_changed'])} closed_or_changed"
    )
    if MANIFEST_PATH.exists():
        print(f"Manifest: {MANIFEST_PATH}")
    print(f"Undo: python scripts/migrate_location_corrections.py --undo")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate competitor location corrections")
    parser.add_argument("--undo", action="store_true", help="Restore pre-migration backups")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    if args.undo:
        undo()
        return

    summary = apply_migrations(dry_run=args.dry_run)
    print_summary(summary)


if __name__ == "__main__":
    main()
