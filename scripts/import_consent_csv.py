#!/usr/bin/env python3
"""
Load a GoHighLevel suppression export into Twenty consent records.

GHL's public API cannot read the suppression list. Export it from the GHL UI
(Settings → Email Services / Unsubscribed, or the suppression CSV) *before*
any termination date. Then:

    python3 scripts/import_consent_csv.py unsubscribed.csv
    python3 scripts/import_consent_csv.py unsubscribed.csv --dry-run

CSV must have an email column (header matching email / Email / EMAIL /
unsubscribed email). Extra columns are ignored. Each address gets a person
(created if missing) and an EMAIL / OPTED_OUT consent record sourced as
IMPORTED_FROM_GHL. Re-running skips addresses that already have a suppressing
consent on email, so a second import is safe.

Does not pull anything from GHL. Does not send mail.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
SUPPRESSED = {"OPTED_OUT", "BOUNCED", "COMPLAINED"}


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for candidate in (ROOT / "infra" / ".env", ROOT / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    for k in ("TWENTY_BASE_URL", "TWENTY_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def twenty(method: str, path: str, key: str, base: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(base.rstrip("/") + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def unwrap(payload: dict, plural: str) -> list[dict]:
    node = payload.get("data", payload) if isinstance(payload, dict) else {}
    rows = node.get(plural) if isinstance(node, dict) else None
    return rows if isinstance(rows, list) else []


def emails_from_csv(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise SystemExit(f"no header row in {path}")
    keys = {n.lower().strip(): n for n in reader.fieldnames if n}
    col = None
    for candidate in ("email", "e-mail", "unsubscribed email", "address"):
        if candidate in keys:
            col = keys[candidate]
            break
    if col is None:
        # Fall back to the first column that looks like it holds addresses.
        for n in reader.fieldnames:
            if n and "mail" in n.lower():
                col = n
                break
    if col is None:
        raise SystemExit(
            f"no email column in {path}. Headers: {', '.join(reader.fieldnames)}")
    seen: set[str] = set()
    out: list[str] = []
    for row in reader:
        raw = (row.get(col) or "").strip().lower()
        if "@" not in raw or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Import a GHL suppression CSV")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.csv.exists():
        print(f"missing {args.csv}", file=sys.stderr)
        return 2

    env = read_env()
    base = env.get("TWENTY_BASE_URL") or "http://localhost:3000"
    key = env.get("TWENTY_API_KEY") or ""
    if not key:
        print("TWENTY_API_KEY is not set in infra/.env", file=sys.stderr)
        return 2

    addresses = emails_from_csv(args.csv)
    print(f"{len(addresses)} unique address(es) in {args.csv}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    created_people = created_consent = skipped = failed = 0

    for email in addresses:
        q = f"emails.primaryEmail[eq]:{parse.quote(email)}"
        status, payload = twenty("GET", f"/rest/people?filter={q}&limit=1",
                                 key, base)
        people = unwrap(payload, "people") if status == 200 else []
        if people:
            pid = people[0]["id"]
        elif args.dry_run:
            print(f"  would create person {email}")
            created_people += 1
            created_consent += 1
            continue
        else:
            status, payload = twenty("POST", "/rest/people", key, base, {
                "name": {"firstName": "Suppressed", "lastName": "Import"},
                "emails": {"primaryEmail": email},
            })
            node = payload.get("data", payload) if isinstance(payload, dict) else {}
            rec = next((v for v in node.values() if isinstance(v, dict)
                        and v.get("id")), {}) if isinstance(node, dict) else {}
            pid = rec.get("id")
            if status >= 400 or not pid:
                failed += 1
                print(f"  FAIL  person {email} HTTP {status}", file=sys.stderr)
                continue
            created_people += 1

        q = f"personId[eq]:{pid},channel[eq]:EMAIL"
        status, payload = twenty(
            "GET", f"/rest/consentRecords?filter={q}&limit=50"
            "&order_by=effectiveAt[DescNullsLast]", key, base)
        consents = unwrap(payload, "consentRecords") if status == 200 else []
        if any((c.get("status") or "") in SUPPRESSED for c in consents):
            skipped += 1
            continue
        if args.dry_run:
            print(f"  would opt out {email}")
            created_consent += 1
            continue
        status, payload = twenty("POST", "/rest/consentRecords", key, base, {
            "name": f"Email consent — {email}",
            "channel": "EMAIL",
            "status": "OPTED_OUT",
            "source": "IMPORTED_FROM_GHL",
            "effectiveAt": now,
            "proof": f"GHL suppression import {args.csv.name}",
            "personId": pid,
        })
        if status >= 400:
            failed += 1
            print(f"  FAIL  consent {email} HTTP {status}", file=sys.stderr)
            continue
        created_consent += 1

    print(f"people created {created_people}  consents written {created_consent}  "
          f"already suppressed {skipped}  failed {failed}"
          + ("  (dry run)" if args.dry_run else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
