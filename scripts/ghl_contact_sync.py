#!/usr/bin/env python3
"""
Recurring GHL -> Twenty contact/company/opportunity sync.

Closes the gap recorded in docs/09-status-and-handover.md: nothing kept
Twenty's CRM data current with GHL, so the continuity fallback was only ever
as fresh as the one-time 18 Aug 2026 pull. This script is meant to run on a
schedule (see infra/ghl_sync.sh) so that on the day GHL is actually needed as
a fallback, Twenty holds this week's data, not August's.

THIS IS A DELIBERATE, NARROW EXCEPTION to CLAUDE.md's "no PII" rule, made
2026-08-25 (see CLAUDE.md, "Disposition values" / hard rule 2). Unlike
scripts/ghl_pull.py, this script pulls actual contact, company and
opportunity records -- names, emails, phones, deal values -- because that is
the data a continuity fallback has to contain. It is still:
  - GET-only against GHL, enforced at the transport layer, same as ghl_pull.py
  - rate-limited the same way
  - scoped to CRM fields only: no conversation bodies, no call recordings,
    no form-submission payloads, no message content
  - never written to a committed file. Data flows GHL -> memory -> Twenty API
    and nothing else; the only thing written to disk is out/ghl_sync_log.json,
    which holds counts and GHL ids, never names/emails/phones, and out/ is
    gitignored except for a short allowlist that does not include this file.

STRATEGY: full re-pull, not delta-fetch. GHL's contacts/opportunities APIs do
not have a verified "updated since" filter (CLAUDE.md's own working-note: the
GHL docs and the live API disagree often enough that a script written blind
against undocumented behaviour fails in ten places at once). Rather than
guess at an incremental filter that might silently miss updates, each run
re-pulls every contact/company/opportunity and upserts by GHL id. This costs
more API calls but cannot go silently stale. Aspire's account is small enough
(low thousands of contacts) that a full pull comfortably fits inside the rate
limit -- re-check that assumption if volume grows by an order of magnitude.

DISCOVERY FIRST. The endpoint paths, list keys and pagination cursors below
are written from GHL's public v2 API documentation, NOT verified against a
live account (this repo currently holds no GHL token -- see
docs/12-ghl-live-pull.md). CLAUDE.md's own findings record a repeated pattern:
a wrong list_key answers HTTP 200 with an empty list, which is silent data
loss dressed up as success. Do not run this unattended before running
`--discover` once against a real token and reading out/ghl_sync_coverage.json.

Usage:
    python scripts/ghl_contact_sync.py --discover      # probe shapes, write nothing
    python scripts/ghl_contact_sync.py --dry-run        # full pull + mapping, no Twenty writes
    python scripts/ghl_contact_sync.py                  # full sync
    python scripts/ghl_contact_sync.py --only companies,people
    python scripts/ghl_contact_sync.py --limit 25        # cap pulled records, for a first test
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
STAGE_MAP = ROOT / "reference" / "ghl_stage_map.yaml"

RATE_LIMIT = 80
RATE_WINDOW = 10.0
MAX_RETRIES = 5
PAGE_SIZE = 100
MAX_PAGES = 500  # hard ceiling: 50k records per object type


class ReadOnlyViolation(RuntimeError):
    """Raised if anything ever attempts a non-GET request against GHL."""


def redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***REDACTED***")
    return text


# --------------------------------------------------------------------------
# Env
# --------------------------------------------------------------------------

def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for dotenv in (ROOT / "infra" / ".env", ROOT / ".env"):
        if dotenv.exists():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    for k in ("GHL_TOKEN", "GHL_LOCATION", "TWENTY_BASE_URL", "TWENTY_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


# --------------------------------------------------------------------------
# GHL: throttled, GET-only
# --------------------------------------------------------------------------

class GHLClient:
    def __init__(self, token: str, base_url: str = "https://services.leadconnectorhq.com",
                 version: str = "2021-07-28"):
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

    def get(self, path: str, query: dict[str, Any]) -> tuple[int, Any, str]:
        url = f"{self._base}{path}"
        if query:
            url = f"{url}?{parse.urlencode({k: v for k, v in query.items() if v is not None})}"
        req = request.Request(url, method="GET")
        if req.get_method() != "GET":
            raise ReadOnlyViolation("non-GET request blocked")
        req.add_header("Accept", "application/json")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Version", self._version)
        req.add_header("User-Agent", "AspireGHLSync/1.0 (+readonly; GET-only)")

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


def dig_first(body: Any, *keys: str) -> Any:
    """Try several candidate list_keys in order; GHL v2 is not consistent
    about the wrapper key across endpoints, and a wrong guess answers 200
    with an empty list rather than an error (see CLAUDE.md, /emails/builder)."""
    if not isinstance(body, dict):
        return None
    for k in keys:
        if k in body and body[k] is not None:
            return body[k]
    return None


# --------------------------------------------------------------------------
# GHL pagination -- cursor style (dateAdded-ordered), per object type
# --------------------------------------------------------------------------

def paginate_cursor(client: GHLClient, path: str, base_query: dict,
                     list_keys: tuple[str, ...], limit: int | None,
                     coverage: dict) -> list[dict]:
    """
    Walks GHL's startAfter/startAfterId cursor pagination. Stops when a page
    comes back shorter than PAGE_SIZE, when the cursor stops advancing (would
    otherwise loop forever on an API that ignores the cursor), or MAX_PAGES
    is hit.
    """
    collected: list[dict] = []
    query = dict(base_query, limit=PAGE_SIZE)
    seen_cursors: set[tuple] = set()

    for page_num in range(MAX_PAGES):
        status, body, err = client.get(path, query)
        if err or status >= 400:
            coverage["error"] = f"{status} {err}"[:300]
            break
        items = dig_first(body, *list_keys)
        if items is None:
            coverage["error"] = (
                f"none of the expected keys {list_keys} found in response; "
                f"top-level keys were {sorted(body.keys()) if isinstance(body, dict) else type(body).__name__}"
            )
            break
        if not isinstance(items, list):
            items = [items]
        collected.extend(items)
        coverage["pages"] = page_num + 1
        coverage["fields"] = sorted(items[0].keys()) if items and isinstance(items[0], dict) else coverage.get("fields", [])

        if limit and len(collected) >= limit:
            return collected[:limit]
        if len(items) < PAGE_SIZE:
            break

        meta = body.get("meta") if isinstance(body, dict) else None
        cursor = None
        if isinstance(meta, dict):
            cursor = (meta.get("startAfter"), meta.get("startAfterId"))
        if not cursor or cursor in seen_cursors or cursor == (None, None):
            coverage["note"] = "stopped: no advancing cursor in response.meta " \
                                "(startAfter/startAfterId) -- pagination may be incomplete"
            break
        seen_cursors.add(cursor)
        query = dict(base_query, limit=PAGE_SIZE,
                     startAfter=cursor[0], startAfterId=cursor[1])

    return collected


# --------------------------------------------------------------------------
# Twenty: read/write REST client
# --------------------------------------------------------------------------

class TwentyClient:
    def __init__(self, base: str, key: str, dry_run: bool):
        self.base = base.rstrip("/")
        self.key = key
        self.dry_run = dry_run
        self.calls = 0

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any, str]:
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        self.calls += 1
        try:
            with request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "null"), ""
        except error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")[:400].replace(self.key, "***")
            return e.code, None, f"HTTP {e.code}: {raw}"
        except Exception as e:  # noqa: BLE001
            return 0, None, str(e).replace(self.key, "***")

    def find_by_ghl_id(self, plural: str, field: str, ghl_id: str) -> str | None:
        q = f"{field}[eq]:{parse.quote(str(ghl_id))}"
        status, body, err = self._request("GET", f"/rest/{plural}?filter={q}&limit=1")
        if status >= 400 or not isinstance(body, dict):
            return None
        rows = ((body.get("data") or {}).get(plural)) or []
        return rows[0]["id"] if rows else None

    def upsert(self, plural: str, ghl_field: str, ghl_id: str, fields: dict) -> tuple[str | None, bool, str]:
        """Returns (twenty_id, created, err). Matches on the GHL id field."""
        existing_id = self.find_by_ghl_id(plural, ghl_field, ghl_id)
        payload = dict(fields)
        payload[ghl_field] = ghl_id
        payload["ghlSyncedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        if self.dry_run:
            return existing_id or "DRY-RUN-ID", existing_id is None, ""

        if existing_id:
            status, _, err = self._request("PATCH", f"/rest/{plural}/{existing_id}", payload)
            if status >= 400:
                return None, False, err
            return existing_id, False, ""

        status, body, err = self._request("POST", f"/rest/{plural}", payload)
        if status >= 400:
            return None, False, err
        node = (body or {}).get("data", body) if isinstance(body, dict) else {}
        rec = next((v for v in node.values() if isinstance(v, dict) and v.get("id")), {}) \
            if isinstance(node, dict) else {}
        rid = rec.get("id")
        if not rid:
            return None, False, "created but no id in response"
        return rid, True, ""


# --------------------------------------------------------------------------
# Stage map (reused from reference/ghl_stage_map.yaml)
# --------------------------------------------------------------------------

def load_stage_map() -> dict[str, dict]:
    """{ghl_stage_id: {"twenty": ..., "drop": bool}}. Empty if PyYAML absent
    or the map cannot be parsed -- callers must treat that as "map nothing",
    never as "map everything to NEW"."""
    if not STAGE_MAP.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        print("WARNING: PyYAML not installed -- opportunity stage mapping "
              "skipped this run (pip install -r scripts/requirements.txt)", file=sys.stderr)
        return {}
    data = yaml.safe_load(STAGE_MAP.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for pipeline in data.get("pipelines", []):
        for stage in pipeline.get("stages", []):
            sid = stage.get("ghl_id")
            if sid:
                out[sid] = {"twenty": stage.get("twenty"), "drop": bool(stage.get("drop"))}
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Recurring GHL -> Twenty CRM data sync")
    ap.add_argument("--discover", action="store_true",
                     help="probe response shapes only, write nothing anywhere, "
                          "prints out/ghl_sync_coverage.json")
    ap.add_argument("--dry-run", action="store_true",
                     help="pull and map records, print what would be written, "
                          "make no Twenty writes")
    ap.add_argument("--only", default="",
                     help="comma-separated: companies,people,opportunities")
    ap.add_argument("--limit", type=int, default=None,
                     help="cap records pulled per object type (testing)")
    args = ap.parse_args()

    wanted = {k.strip() for k in args.only.split(",") if k.strip()} or \
        {"companies", "people", "opportunities"}

    env = read_env()
    ghl_token, ghl_location = env.get("GHL_TOKEN"), env.get("GHL_LOCATION")
    twenty_base = env.get("TWENTY_BASE_URL", "http://localhost:3000")
    twenty_key = env.get("TWENTY_API_KEY")

    if not ghl_token or not ghl_location:
        print("ERROR: GHL_TOKEN and GHL_LOCATION must be set in .env.", file=sys.stderr)
        return 2
    if not args.discover and not twenty_key and not args.dry_run:
        print("ERROR: TWENTY_API_KEY not set (infra/.env or .env).", file=sys.stderr)
        return 2

    ghl = GHLClient(ghl_token)
    twenty = TwentyClient(twenty_base, twenty_key or "DRY-RUN", args.dry_run)
    started = datetime.now(timezone.utc)

    OUT.mkdir(exist_ok=True)
    coverage: dict[str, dict] = {}
    counts = {"companies": 0, "people": 0, "opportunities": 0}
    errors: list[str] = []

    # ---- companies (GHL "businesses") ----
    if "companies" in wanted:
        cov: dict = {}
        records = paginate_cursor(
            ghl, "/businesses/", {"locationId": ghl_location},
            ("businesses",), args.limit, cov)
        coverage["companies"] = cov
        print(f"companies: {len(records)} pulled from GHL"
              + (f" -- {cov['error']}" if cov.get("error") else ""))
        if cov.get("error"):
            errors.append(f"companies: {cov['error']}")
        if not args.discover:
            for biz in records:
                gid = biz.get("id")
                if not gid:
                    continue
                fields = {
                    "name": biz.get("name") or "Unnamed (GHL)",
                    "domainName": {"primaryLinkUrl": biz.get("website")} if biz.get("website") else None,
                    "address": {"addressStreet1": biz.get("address")} if biz.get("address") else None,
                }
                fields = {k: v for k, v in fields.items() if v is not None}
                _, _, err = twenty.upsert("companies", "ghlBusinessId", gid, fields)
                if err:
                    errors.append(f"company {gid}: {err[:150]}")
                else:
                    counts["companies"] += 1

    # ---- people (GHL "contacts") ----
    if "people" in wanted:
        cov = {}
        records = paginate_cursor(
            ghl, "/contacts/", {"locationId": ghl_location},
            ("contacts",), args.limit, cov)
        coverage["people"] = cov
        print(f"people: {len(records)} pulled from GHL"
              + (f" -- {cov['error']}" if cov.get("error") else ""))
        if cov.get("error"):
            errors.append(f"people: {cov['error']}")
        if not args.discover:
            for contact in records:
                gid = contact.get("id")
                if not gid:
                    continue
                name = {
                    "firstName": contact.get("firstName") or "",
                    "lastName": contact.get("lastName") or "",
                }
                fields: dict[str, Any] = {"name": name}
                if contact.get("email"):
                    fields["emails"] = {"primaryEmail": contact["email"]}
                if contact.get("phone"):
                    fields["phones"] = {"primaryPhoneNumber": contact["phone"]}
                company_gid = contact.get("companyId")
                if company_gid:
                    tid = twenty.find_by_ghl_id("companies", "ghlBusinessId", company_gid)
                    if tid:
                        fields["companyId"] = tid
                _, _, err = twenty.upsert("people", "ghlContactId", gid, fields)
                if err:
                    errors.append(f"person {gid}: {err[:150]}")
                else:
                    counts["people"] += 1

    # ---- opportunities ----
    if "opportunities" in wanted:
        cov = {}
        records = paginate_cursor(
            ghl, "/opportunities/search", {"location_id": ghl_location},
            ("opportunities",), args.limit, cov)
        coverage["opportunities"] = cov
        print(f"opportunities: {len(records)} pulled from GHL"
              + (f" -- {cov['error']}" if cov.get("error") else ""))
        if cov.get("error"):
            errors.append(f"opportunities: {cov['error']}")
        if not args.discover:
            stage_map = load_stage_map()
            dropped = 0
            for opp in records:
                gid = opp.get("id")
                if not gid:
                    continue
                stage_id = opp.get("pipelineStageId")
                mapped = stage_map.get(stage_id) if stage_map else None
                if mapped and mapped.get("drop"):
                    dropped += 1
                    continue
                fields: dict[str, Any] = {"name": opp.get("name") or "Untitled (GHL)"}
                if opp.get("monetaryValue") is not None:
                    fields["amount"] = {"amountMicros": int(float(opp["monetaryValue"]) * 1_000_000),
                                        "currencyCode": "USD"}
                if mapped and mapped.get("twenty"):
                    fields["stage"] = mapped["twenty"]
                contact_gid = opp.get("contactId")
                if contact_gid:
                    tid = twenty.find_by_ghl_id("people", "ghlContactId", contact_gid)
                    if tid:
                        fields["pointOfContactId"] = tid
                _, _, err = twenty.upsert("opportunities", "ghlOpportunityId", gid, fields)
                if err:
                    errors.append(f"opportunity {gid}: {err[:150]}")
                else:
                    counts["opportunities"] += 1
            if dropped:
                print(f"opportunities: {dropped} skipped (terminal/non-progressing "
                      f"stage per reference/ghl_stage_map.yaml drop: true)")

    finished = datetime.now(timezone.utc)
    log = {
        "startedAt": started.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "finishedAt": finished.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "mode": "discover" if args.discover else ("dry-run" if args.dry_run else "live"),
        "ghl_requests": ghl.request_count,
        "twenty_requests": twenty.calls,
        "counts": counts,
        "coverage": coverage,
        "errors": errors,
    }
    if args.discover:
        dest = OUT / "ghl_sync_coverage.json"
    else:
        dest = OUT / "ghl_sync_log.json"
    dest.write_text(json.dumps(log, indent=2), encoding="utf-8")

    if args.discover:
        print(f"\nDiscovery complete. Read {dest.relative_to(ROOT)} before trusting the "
              f"list_keys and pagination in this script against a real account.")
        return 0

    # Write the run into Twenty's own execution history, same discipline every
    # n8n workflow follows -- this sync is not exempt from being auditable.
    if not args.dry_run and twenty_key:
        twenty._request("POST", "/rest/automationRuns", {  # noqa: SLF001
            "workflowName": "GHL Contact Sync (cron)",
            "status": "Failed" if errors else "Success",
            "startedAt": log["startedAt"],
            "durationMs": int((finished - started).total_seconds() * 1000),
            "errorMessage": "; ".join(errors)[:2000] if errors else None,
        })

    print(f"\n{'DRY RUN -- ' if args.dry_run else ''}"
          f"companies {counts['companies']} · people {counts['people']} · "
          f"opportunities {counts['opportunities']} · {len(errors)} error(s) · "
          f"{ghl.request_count} GHL call(s) · {twenty.calls} Twenty call(s)")
    print(f"Log: {dest.relative_to(ROOT)}")
    if errors:
        print("Errors (GHL ids only, no names/emails logged):", file=sys.stderr)
        for e in errors[:20]:
            print(f"  {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
