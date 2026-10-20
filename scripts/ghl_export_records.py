#!/usr/bin/env python3
"""
Export GoHighLevel RECORDS (not configuration) for the Phase 4 migration.

Unlike ghl_pull.py, this keeps record bodies: contacts, opportunities,
businesses, users, plus the pipelines, tags and custom-field definitions needed
to map them. It is still GET-only — it reuses ghl_pull.Client, which refuses
any other method at the transport layer.

Approved by Aspire management for the migration (Oct 2026). Output contains
customer PII and goes to raw/records/, which is gitignored. Never commit it,
and delete it from any machine that does not need it once the import is done.

Every paged collection is checked against GHL's own meta.total: a silent short
read (CLAUDE.md, "a silent zero behind a 200") fails the export rather than
producing a partial migration that looks complete.

Usage:
    python3 scripts/ghl_export_records.py              # everything
    python3 scripts/ghl_export_records.py --only contacts,opportunities
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ghl_pull import Client, read_env  # noqa: E402  (GET-only transport)

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "raw" / "records"
SUMMARY = ROOT / "out" / "record_export.json"
BASE_URL = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
PAGE = 100


def write_json(name: str, data) -> None:
    (OUT_DIR / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def paged(client: Client, path: str, query: dict, key: str) -> tuple[list, int | None]:
    """Follow startAfter/startAfterId paging until GHL returns an empty page."""
    rows: list = []
    total = None
    q = dict(query, limit=PAGE)
    seen: set[str] = set()
    while True:
        status, body, err = client.get(path, q)
        if status != 200 or body is None:
            raise SystemExit(f"  FAIL  {path}: {err or status}")
        page = body.get(key) or []
        meta = body.get("meta") or {}
        if total is None:
            total = meta.get("total")
        new = [r for r in page if r.get("id") not in seen]
        seen.update(r.get("id") for r in new)
        rows.extend(new)
        print(f"\r  ..    {key:<14} {len(rows)}/{total}", end="", flush=True)
        after, after_id = meta.get("startAfter"), meta.get("startAfterId")
        if not page or not new or after_id is None:
            break
        q = dict(query, limit=PAGE, startAfter=after, startAfterId=after_id)
    print()
    return rows, total


def main() -> int:
    ap = argparse.ArgumentParser(description="Export GHL records (read-only)")
    ap.add_argument("--only", default="", help="comma list: contacts,opportunities,reference")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    env = read_env()
    token, loc = env.get("GHL_TOKEN", ""), env.get("GHL_LOCATION", "")
    if not token or set(token[4:]) <= {"x"} or not loc:
        print("GHL_TOKEN and GHL_LOCATION must be set in .env", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = Client(token, BASE_URL, VERSION)
    summary: dict = {"exported_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "location": loc, "counts": {}, "complete": True}

    def want(name: str) -> bool:
        return not only or name in only

    if want("reference"):
        refs = {
            "pipelines.json": ("/opportunities/pipelines", {"locationId": loc}, "pipelines"),
            "users.json": ("/users/", {"locationId": loc}, "users"),
            "businesses.json": ("/businesses/", {"locationId": loc}, "businesses"),
            "tags.json": (f"/locations/{loc}/tags", {}, "tags"),
            "custom_fields.json": (f"/locations/{loc}/customFields", {}, "customFields"),
        }
        for fname, (path, q, key) in refs.items():
            status, body, err = client.get(path, q)
            if status != 200 or body is None:
                print(f"  FAIL  {key}: {err or status}")
                return 1
            data = body.get(key) or []
            write_json(fname, data)
            summary["counts"][key] = len(data)
            print(f"  ok    {key:<14} {len(data)}")

    for name, path, q, key in (
        ("contacts", "/contacts/", {"locationId": loc}, "contacts"),
        ("opportunities", "/opportunities/search", {"location_id": loc}, "opportunities"),
    ):
        if not want(name):
            continue
        rows, total = paged(client, path, q, key)
        write_json(f"{name}.json", rows)
        summary["counts"][name] = len(rows)
        summary["counts"][f"{name}_ghl_total"] = total
        if total is not None and len(rows) != total:
            summary["complete"] = False
            print(f"  FAIL  {name}: exported {len(rows)} but GHL reports {total}")
        else:
            print(f"  ok    {name:<14} {len(rows)} (matches GHL total)")

    SUMMARY.parent.mkdir(exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\n  {client.request_count} GET request(s). Records in raw/records/ (gitignored).")
    print(f"  Counts only: {SUMMARY.relative_to(ROOT)}")
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
