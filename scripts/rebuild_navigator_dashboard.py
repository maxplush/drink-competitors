#!/usr/bin/env python3
"""List data sources, apply location migrations, rebuild navigator dashboard, verify HTML."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DASHBOARD_HTML = DATA / "competitors_dashboard.html"
MAP_SCRIPT = ROOT / "map_locations.py"
MIGRATE_SCRIPT = ROOT / "scripts" / "migrate_location_corrections.py"
UF_JSON = DATA / "unified_ferments_locations.json"
NON_JSON = DATA / "non_locations.json"
MIGRATION_TARGETS = [UF_JSON, NON_JSON]

GREP_PATTERNS = ("80 Pine", "70 Pine", "Hair Saga", "10000")


def row_count(path: Path) -> str:
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return str(len(payload))
            if isinstance(payload, dict):
                return f"dict keys={len(payload)}"
            return type(payload).__name__
        except json.JSONDecodeError:
            return "invalid JSON"
    if path.suffix == ".csv":
        text = path.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return str(max(0, len(lines) - 1)) if lines else "0"
    if path.suffix == ".jsonl":
        return str(sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()))
    return "—"


def step1_list_data_files() -> None:
    print("=== 1. data/ files and row counts ===")
    for path in sorted(DATA.iterdir()):
        if path.is_file():
            print(f"  {path.name}: {row_count(path)} rows")
    print()
    print("Navigator dashboard source: ALL data/*_locations.json merged by map_locations.load_locations()")
    print("Primary migration targets: unified_ferments_locations.json, non_locations.json")
    print()


def step2_crown_shy_raw() -> None:
    print("=== 2. CROWN SHY record(s) in unified_ferments_locations.json (raw) ===")
    rows = json.loads(UF_JSON.read_text(encoding="utf-8"))
    found = False
    for row in rows:
        if (row.get("name") or "").upper() == "CROWN SHY":
            print(json.dumps(row, ensure_ascii=False, indent=2))
            found = True
    if not found:
        print("(no CROWN SHY row in unified_ferments_locations.json)")
    print()


def step3_build_script() -> None:
    print("=== 3. Dashboard build script ===")
    print(f"  path: {MAP_SCRIPT}")
    print(f"  command: python {MAP_SCRIPT.relative_to(ROOT)}")
    print(f"  output: {DASHBOARD_HTML}")
    print()


def step4_backup_and_migrate() -> None:
    print("=== 4. Backup + apply migrations ===")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = DATA / "migrations" / f"navigator_rebuild_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in MIGRATION_TARGETS:
        if path.exists():
            dest = backup_dir / path.name
            shutil.copy2(path, dest)
            print(f"  backed up {path.name} -> {dest}")
    result = subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    print()


def step5_rebuild_dashboard() -> None:
    print("=== 5. Regenerate dashboard HTML ===")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "folium", "branca"],
        cwd=ROOT,
        check=True,
    )
    result = subprocess.run(
        [sys.executable, str(MAP_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    print()


def grep_dashboard() -> None:
    if not DASHBOARD_HTML.exists():
        print(f"ERROR: {DASHBOARD_HTML} not found")
        raise SystemExit(1)
    text = DASHBOARD_HTML.read_text(encoding="utf-8")
    for pattern in GREP_PATTERNS:
        print(f"--- grep '{pattern}' {DASHBOARD_HTML.name} ---")
        hit_count = text.count(pattern)
        line_hits: list[str] = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                if len(line) > 240:
                    line_hits.append(f"{lineno}:{line[:240]}... ({line.count(pattern)} occurrences on line)")
                else:
                    line_hits.append(f"{lineno}:{line}")
        if hit_count == 0:
            print("(0 hits)")
        else:
            print(f"({hit_count} total occurrences)")
            for h in line_hits:
                print(h)
        print()


def main() -> None:
    step1_list_data_files()
    step2_crown_shy_raw()
    step3_build_script()
    step4_backup_and_migrate()
    step5_rebuild_dashboard()
    grep_dashboard()


if __name__ == "__main__":
    main()
