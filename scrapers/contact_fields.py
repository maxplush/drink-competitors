"""Split legacy mixed contact strings into phone and email fields."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+", re.I)
PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}"
)
HANDLE_RE = re.compile(r"^@[\w.]+$")


def split_contact(raw: str) -> tuple[str, str]:
    """Return (phones, emails) joined with ' | ' when multiple values exist."""
    if not raw or not raw.strip():
        return "", ""

    phones: list[str] = []
    emails: list[str] = []
    seen_phones: set[str] = set()
    seen_emails: set[str] = set()

    for part in re.split(r"\s*[|,]\s*", raw.strip()):
        part = part.strip()
        if not part:
            continue

        email_match = EMAIL_RE.search(part)
        if email_match:
            email = email_match.group(0)
            if email.lower() not in seen_emails:
                seen_emails.add(email.lower())
                emails.append(email)
            continue

        if HANDLE_RE.match(part):
            if part.lower() not in seen_emails:
                seen_emails.add(part.lower())
                emails.append(part)
            continue

        phone_match = PHONE_RE.search(part)
        if phone_match:
            phone = phone_match.group(0).strip()
            key = re.sub(r"\D", "", phone)
            if key not in seen_phones:
                seen_phones.add(key)
                phones.append(phone)
            continue

    return " | ".join(phones), " | ".join(emails)


def row_phone_email(row: dict[str, Any]) -> tuple[str, str]:
    phone = (row.get("phone") or "").strip()
    email = (row.get("email") or "").strip()
    if phone or email:
        return phone, email
    return split_contact(row.get("contact") or "")


def normalize_row_contact(row: dict[str, Any]) -> bool:
    """Move legacy `contact` into `phone` / `email`; free-text goes to notes."""
    changed = False
    phone = (row.get("phone") or "").strip()
    email = (row.get("email") or "").strip()
    contact = (row.get("contact") or "").strip()

    if contact:
        parsed_phone, parsed_email = split_contact(contact)
        if parsed_phone and not phone:
            row["phone"] = parsed_phone
            phone = parsed_phone
            changed = True
        if parsed_email and not email:
            row["email"] = parsed_email
            email = parsed_email
            changed = True

        leftover = contact
        for value in (parsed_phone, parsed_email):
            for piece in value.split(" | "):
                leftover = leftover.replace(piece, "")
        leftover = re.sub(r"\s*[|,]\s*", " ", leftover).strip()

        if parsed_phone or parsed_email:
            row["contact"] = ""
            changed = True
        elif leftover:
            notes = (row.get("notes") or "").strip()
            note_line = f"Contact: {leftover}"
            if note_line not in notes:
                row["notes"] = f"{notes}\n{note_line}".strip() if notes else note_line
            row["contact"] = ""
            changed = True

    if "phone" not in row:
        row["phone"] = phone
        changed = True
    if "email" not in row:
        row["email"] = email
        changed = True

    return changed


def save_json_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = path.with_suffix(".csv")
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def migrate_all(data_dir: Path) -> int:
    updated_files = 0
    for path in sorted(data_dir.glob("*_locations.json")):
        rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for row in rows:
            if normalize_row_contact(row):
                changed = True
        if changed:
            save_json_csv(path, rows)
            updated_files += 1
            print(f"Migrated {path.name}")
    return updated_files


def main() -> None:
    data_dir = Path(__file__).resolve().parents[1] / "data"
    count = migrate_all(data_dir)
    print(f"Done — updated {count} file(s)")


if __name__ == "__main__":
    main()
