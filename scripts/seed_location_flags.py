#!/usr/bin/env python3
"""Apply verified / needs_review flags to location JSON sources."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scrapers.contact_fields import save_json_csv
from scrapers.location_merge import (
    apply_verified_and_review_flags,
    count_verified,
    load_json_rows,
)

TARGETS = [
    ROOT / "data" / "unified_ferments_locations.json",
    ROOT / "data" / "non_locations.json",
]


def main() -> None:
    for path in TARGETS:
        rows = load_json_rows(path)
        before = count_verified(rows)
        rows = apply_verified_and_review_flags(rows)
        after = count_verified(rows)
        save_json_csv(path, rows)
        flagged = sum(1 for r in rows if r.get("needs_review"))
        print(f"{path.name}: verified {before} -> {after}, needs_review={flagged}")


if __name__ == "__main__":
    main()
