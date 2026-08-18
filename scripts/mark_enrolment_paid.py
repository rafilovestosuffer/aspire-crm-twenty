#!/usr/bin/env python3
"""
Mark a bootcamp enrolment PAID after money actually cleared.

ENR Bootcamp Enrolment creates the row as PAYMENT_SENT and will not flip it
to PAID just because a FastPayDirect link was emailed. Joining instructions
only fire on PAID, so someone has to confirm. Until FastPayDirect (or a
human) posts here, that someone is you:

    python3 scripts/mark_enrolment_paid.py --email student@example.com --yes

Looks up the person, finds enrollments in PAYMENT_SENT, patches them to PAID.
Refuses to run without --yes. Does not send mail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent


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
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw or "null")
        except json.JSONDecodeError:
            parsed = {"error": raw[:200]}
        return e.code, parsed


def unwrap(payload: dict, plural: str) -> list[dict]:
    node = payload.get("data", payload) if isinstance(payload, dict) else {}
    rows = node.get(plural) if isinstance(node, dict) else None
    return rows if isinstance(rows, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(description="Mark an enrolment PAID")
    ap.add_argument("--email", required=True)
    ap.add_argument("--yes", action="store_true",
                    help="required: actually write PAID")
    args = ap.parse_args()
    email = args.email.strip().lower()
    if "@" not in email:
        print("not an email", file=sys.stderr)
        return 2

    env = read_env()
    base = env.get("TWENTY_BASE_URL") or "http://localhost:3000"
    key = env.get("TWENTY_API_KEY") or ""
    if not key:
        print("TWENTY_API_KEY is not set", file=sys.stderr)
        return 2

    q = f"emails.primaryEmail[eq]:{parse.quote(email)}"
    status, payload = twenty("GET", f"/rest/people?filter={q}&limit=1", key, base)
    people = unwrap(payload, "people") if status == 200 else []
    if not people:
        print(f"no person for {email}", file=sys.stderr)
        return 1
    pid = people[0]["id"]

    q = f"personId[eq]:{pid},status[eq]:PAYMENT_SENT"
    status, payload = twenty(
        "GET", f"/rest/enrollments?filter={q}&limit=50", key, base)
    rows = unwrap(payload, "enrollments") if status == 200 else []
    if not rows:
        print(f"no PAYMENT_SENT enrolment for {email}")
        return 1

    for e in rows:
        print(f"  {e.get('id')}  {e.get('name') or e.get('tier')}  PAYMENT_SENT")
    if not args.yes:
        print("pass --yes to mark these PAID")
        return 2

    failed = 0
    for e in rows:
        status, _ = twenty("PATCH", f"/rest/enrollments/{e['id']}", key, base,
                           {"status": "PAID"})
        if status >= 400:
            failed += 1
            print(f"  FAIL  {e['id']} HTTP {status}", file=sys.stderr)
        else:
            print(f"  ok    {e['id']} → PAID")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
