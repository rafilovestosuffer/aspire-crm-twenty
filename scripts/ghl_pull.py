#!/usr/bin/env python3
"""
Read-only GoHighLevel configuration puller.

Walks reference/ghl_endpoints.yaml, issues GET requests only, and writes one
JSON file per endpoint into raw/. Endpoints classified `volume` have their
bodies discarded — only counts and field names are retained.

The first run is a DISCOVERY run: every endpoint's HTTP status and response
shape is recorded in out/pull_coverage.csv. Read that before trusting anything.

Usage:
    python scripts/ghl_pull.py --dry-run          # show the plan, call nothing
    python scripts/ghl_pull.py                    # pull everything not yet pulled
    python scripts/ghl_pull.py --only workflows,forms
    python scripts/ghl_pull.py --force            # re-pull endpoints already on disk
    python scripts/ghl_pull.py --window-days 90   # evidence window (default 90)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "reference" / "ghl_endpoints.yaml"
RAW = ROOT / "raw"
OUT = ROOT / "out"

# Requests per window, per GHL's published ceiling (100 req / 10 s).
# Deliberately conservative: 80 leaves headroom for anything else on the token.
RATE_LIMIT = 80
RATE_WINDOW = 10.0
MAX_RETRIES = 5


# --------------------------------------------------------------------------
# Safety guards
# --------------------------------------------------------------------------

class ReadOnlyViolation(RuntimeError):
    """Raised if anything ever attempts a non-GET request."""


def redact(text: str, *secrets: str) -> str:
    """Strip token material from anything that might be printed or logged."""
    for s in secrets:
        if s:
            text = text.replace(s, "***REDACTED***")
    return text


# --------------------------------------------------------------------------
# Minimal YAML reader
# --------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    """Parse the registry. Uses PyYAML when present, else a scoped fallback."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        pass

    # Fallback: handles the subset of YAML this registry uses — nested maps,
    # a list of maps, inline {k: v} flow maps, and folded (>) scalars.
    def coerce(v: str) -> Any:
        v = v.strip()
        if v.startswith("{") and v.endswith("}"):
            out: dict[str, Any] = {}
            body = v[1:-1].strip()
            if body:
                for part in split_flow(body):
                    k, _, val = part.partition(":")
                    out[k.strip()] = coerce(val)
            return out
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            return v[1:-1]
        if v in ("true", "True"):
            return True
        if v in ("false", "False"):
            return False
        if v.isdigit():
            return int(v)
        return v

    def split_flow(body: str) -> list[str]:
        parts, depth, cur = [], 0, ""
        for ch in body:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            parts.append(cur)
        return parts

    root: dict[str, Any] = {}
    endpoints: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section = None
    fold_key: str | None = None
    fold_buf: list[str] = []
    fold_indent = 0

    for raw_line in text.splitlines():
        line = raw_line.split(" #")[0].rstrip() if not raw_line.strip().startswith("#") else ""
        if not line.strip():
            if fold_key:
                fold_buf.append("")
            continue

        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if fold_key is not None:
            if indent >= fold_indent:
                fold_buf.append(stripped)
                continue
            assert current is not None
            current[fold_key] = " ".join(x for x in fold_buf if x)
            fold_key, fold_buf = None, []

        if stripped in ("defaults:", "endpoints:"):
            section = stripped[:-1]
            continue

        if stripped.startswith("- "):
            current = {}
            endpoints.append(current)
            stripped = stripped[2:]

        key, _, value = stripped.partition(":")
        key, value = key.strip(), value.strip()

        if value == ">":
            fold_key, fold_indent = key, indent + 2
            continue

        target = current if section == "endpoints" and current is not None else root.setdefault("defaults", {})
        if section == "defaults":
            target = root.setdefault("defaults", {})
        target[key] = coerce(value)

    if fold_key is not None and current is not None:
        current[fold_key] = " ".join(x for x in fold_buf if x)

    root["endpoints"] = endpoints
    return root


# --------------------------------------------------------------------------
# Throttled, GET-only HTTP
# --------------------------------------------------------------------------

class Client:
    def __init__(self, token: str, base_url: str, version: str):
        self._token = token
        self._base = base_url.rstrip("/")
        self._version = version
        self._calls: deque[float] = deque()
        self.request_count = 0

    def _throttle(self) -> None:
        now = time.monotonic()
        while self._calls and now - self._calls[0] > RATE_WINDOW:
            self._calls.popleft()
        if len(self._calls) >= RATE_LIMIT:
            sleep_for = RATE_WINDOW - (now - self._calls[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._calls.append(time.monotonic())

    def get(self, path: str, query: dict[str, Any], version: str | None = None
            ) -> tuple[int, Any, str]:
        """Return (status, parsed_body_or_None, error_message). Never raises on HTTP errors."""
        url = f"{self._base}{path}"
        if query:
            url = f"{url}?{parse.urlencode({k: v for k, v in query.items() if v is not None})}"

        req = request.Request(url, method="GET")
        if req.get_method() != "GET":
            raise ReadOnlyViolation("non-GET request blocked")
        req.add_header("Accept", "application/json")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Version", version or self._version)

        for attempt in range(MAX_RETRIES):
            self._throttle()
            self.request_count += 1
            try:
                with request.urlopen(req, timeout=60) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8") or "null"), ""
            except error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:400]
                if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                return e.code, None, redact(f"HTTP {e.code}: {body}", self._token)
            except (error.URLError, TimeoutError) as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                return 0, None, redact(f"network: {e}", self._token)
            except json.JSONDecodeError as e:
                return 0, None, f"invalid JSON: {e}"
        return 0, None, "exhausted retries"


# --------------------------------------------------------------------------
# Response handling
# --------------------------------------------------------------------------

def dig(body: Any, dotted: str | None) -> Any:
    if not dotted or body is None:
        return body
    cur = body
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def shape_of(records: Any) -> list[str]:
    """Field names present, so we know what we got without storing what we got."""
    if isinstance(records, list) and records and isinstance(records[0], dict):
        return sorted(records[0].keys())
    if isinstance(records, dict):
        return sorted(records.keys())
    return []


def summarise_volume(records: Any) -> dict:
    """For PII-bearing endpoints: keep the count and the schema, drop the data."""
    items = records if isinstance(records, list) else ([] if records is None else [records])
    return {
        "_class": "volume",
        "_note": "Bodies discarded at pull time. Counts and field names only.",
        "count": len(items),
        "fields": shape_of(items),
    }


def paginate(client: Client, ep: dict, base_query: dict, page_size: int,
             version: str) -> tuple[int, list, str]:
    """Walk pages according to the registry's paginate style."""
    style = ep.get("paginate", "none")
    path, list_key = ep["path"], ep.get("list_key")
    collected: list = []
    status, err = 0, ""

    if style == "none":
        status, body, err = client.get(path, base_query, version)
        got = dig(body, list_key)
        if isinstance(got, list):
            collected = got
        elif got is not None:
            collected = [got]
        return status, collected, err

    param = {"limit_skip": "skip", "limit_offset": "offset", "limit_page": "page"}[style]
    cursor = 1 if param == "page" else 0

    for _ in range(200):  # hard ceiling: 20k records per endpoint
        q = dict(base_query, limit=page_size, **{param: cursor})
        status, body, err = client.get(path, q, version)
        if err or status >= 400:
            break
        page = dig(body, list_key)
        if not isinstance(page, list) or not page:
            break
        collected.extend(page)
        if len(page) < page_size:
            break
        cursor += 1 if param == "page" else page_size

    return status, collected, err


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def read_env() -> dict[str, str]:
    env = {}
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip("\"'")
    for k in ("GHL_TOKEN", "GHL_LOCATION", "GHL_COMPANY_ID"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only GHL configuration puller")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, make no calls")
    ap.add_argument("--only", default="", help="comma-separated endpoint keys")
    ap.add_argument("--force", action="store_true", help="re-pull endpoints already on disk")
    ap.add_argument("--window-days", type=int, default=90, help="evidence window (default 90)")
    args = ap.parse_args()

    reg = load_yaml(REGISTRY)
    defaults = reg.get("defaults", {})
    endpoints = reg.get("endpoints", [])

    wanted = {k.strip() for k in args.only.split(",") if k.strip()}
    if wanted:
        endpoints = [e for e in endpoints if e.get("key") in wanted]

    if args.dry_run:
        print(f"{len(endpoints)} endpoint(s) registered\n")
        for e in endpoints:
            flag = " [AGENCY]" if e.get("class") == "agency" else ""
            flag += " [UNVERIFIED]" if e.get("unverified") else ""
            print(f"  {e.get('key'):26} {e.get('class'):7} GET {e.get('path')}{flag}")
        print("\nDry run — no requests made.")
        return 0

    env = read_env()
    token, location = env.get("GHL_TOKEN"), env.get("GHL_LOCATION")
    company = env.get("GHL_COMPANY_ID")
    if not token or not location:
        print("ERROR: GHL_TOKEN and GHL_LOCATION must be set in .env "
              "(copy .env.example). See docs/01-scope-and-questions.md.", file=sys.stderr)
        return 2

    RAW.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=args.window_days)
    substitutions = {
        "locationId": location,
        "companyId": company or "",
        "window_start_ms": str(int(start.timestamp() * 1000)),
        "window_end_ms": str(int(now.timestamp() * 1000)),
        "window_start_date": start.strftime("%Y-%m-%d"),
        "window_end_date": now.strftime("%Y-%m-%d"),
    }

    client = Client(token, defaults.get("base_url", ""), str(defaults.get("version", "2021-07-28")))
    page_size = int(defaults.get("page_size", 100))
    coverage: list[dict] = []

    for ep in endpoints:
        key, cls = ep["key"], ep.get("class", "config")
        dest = RAW / f"{key}.json"

        if cls == "agency" and not company:
            coverage.append({"endpoint": key, "area": ep.get("area", ""), "class": cls,
                             "status": "skipped", "count": "", "fields": "",
                             "note": "agency token / GHL_COMPANY_ID not provided"})
            print(f"  skip  {key:26} (agency-level, no companyId)")
            continue

        if dest.exists() and not args.force:
            print(f"  have  {key:26} (use --force to re-pull)")
            continue

        path = ep["path"].format(**substitutions)
        query = {k: str(v).format(**substitutions) for k, v in (ep.get("query") or {}).items()}
        version = str(ep.get("version", defaults.get("version", "2021-07-28")))

        status, records, err = paginate(client, dict(ep, path=path), query, page_size, version)

        if err or status >= 400:
            coverage.append({"endpoint": key, "area": ep.get("area", ""), "class": cls,
                             "status": f"error {status}", "count": "", "fields": "",
                             "note": err[:200]})
            marker = "note " if ep.get("unverified") else "FAIL "
            print(f"  {marker} {key:26} {status} {err[:90]}")
            continue

        payload = summarise_volume(records) if cls == "volume" else records
        dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        count = len(records) if isinstance(records, list) else 1
        coverage.append({"endpoint": key, "area": ep.get("area", ""), "class": cls,
                         "status": "ok", "count": count,
                         "fields": "|".join(shape_of(records)), "note": ""})
        print(f"  ok    {key:26} {count:>5} record(s)")

    cov_path = OUT / "pull_coverage.csv"
    with cov_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["endpoint", "area", "class", "status",
                                           "count", "fields", "note"])
        w.writeheader()
        w.writerows(coverage)

    ok = sum(1 for c in coverage if c["status"] == "ok")
    print(f"\n{ok}/{len(coverage)} endpoint(s) returned data. "
          f"{client.request_count} request(s) made.")
    print(f"Coverage report: {cov_path.relative_to(ROOT)}")
    print("Read the coverage report before drawing any conclusion from raw/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
