#!/usr/bin/env python3
"""Run NON scraper merge against a copy; confirm verified CROWN SHY address survives."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.contact_fields import save_json_csv
from scrapers.location_merge import (
    apply_verified_and_review_flags,
    load_json_rows,
    merge_scraped_into_existing,
    non_row_key,
)
from scrapers.non import scrape

DATA = ROOT / "data"
SRC = DATA / "non_locations.json"
COPY_DIR = DATA / "test_merge"
COPY = COPY_DIR / "non_locations.json"


def main() -> None:
    COPY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, COPY)

    existing = load_json_rows(COPY)
    crown_before = next(
        (r for r in existing if (r.get("name") or "").upper() == "CROWN SHY"),
        None,
    )
    print("=== BEFORE merge (copy) ===")
    print(json.dumps(crown_before, ensure_ascii=False, indent=2))

    scraped = scrape()
    merged = merge_scraped_into_existing(existing, scraped, non_row_key)
    merged = apply_verified_and_review_flags(merged)
    save_json_csv(COPY, merged)

    crown_after = next(
        (r for r in merged if (r.get("name") or "").upper() == "CROWN SHY"),
        None,
    )
    print("\n=== AFTER scrape + merge (copy) ===")
    print(json.dumps(crown_after, ensure_ascii=False, indent=2))

    addr = crown_after.get("address") or ""
    if "70 Pine" not in addr:
        raise SystemExit(f"CROWN SHY address lost verified street: {addr!r}")
    if not crown_after.get("verified"):
        raise SystemExit("CROWN SHY verified flag not preserved")
    scraped_addr = crown_after.get("scraped_address") or ""
    print(f"\nOK: address contains '70 Pine'; scraped_address={scraped_addr!r}")


if __name__ == "__main__":
    main()
