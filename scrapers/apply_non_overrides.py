"""Apply manual NON address overrides to existing non_locations.json."""

from __future__ import annotations

import json
from pathlib import Path

from scrapers.non import apply_address_overrides, save


def main() -> None:
    data_path = Path(__file__).resolve().parents[1] / "data" / "non_locations.json"
    rows = json.loads(data_path.read_text(encoding="utf-8"))
    rows = apply_address_overrides(rows)
    json_path, csv_path = save(rows, data_path.parent)
    print(f"Applied NON overrides -> {json_path}, {csv_path}")


if __name__ == "__main__":
    main()
