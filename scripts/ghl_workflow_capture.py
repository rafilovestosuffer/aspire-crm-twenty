#!/usr/bin/env python3
"""
Tier 4 — capture workflow internals.

GHL's public API returns workflow metadata only; step trees are not on any
documented endpoint (docs/02 §1). The working route is to replay the request the
GHL UI itself makes, using your browser session.

  1. Open any workflow in GHL.
  2. DevTools → Network → XHR. Find the response containing the step tree.
  3. Right-click → Copy → Copy as cURL.
  4. Save it to .session_curl.txt in the repo root (gitignored).
  5. Run this script.

It substitutes each workflow id into that request and saves one JSON per
workflow. The session JWT expires in hours, not days, so the run is
CHECKPOINTED: completed workflows are skipped on restart. Expect to re-capture
the cURL once or twice during a full run. That is normal, not a failure.

Usage:
    python scripts/ghl_workflow_capture.py --dry-run
    python scripts/ghl_workflow_capture.py
    python scripts/ghl_workflow_capture.py --limit 5     # verify shape first
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
CURL_FILE = ROOT / ".session_curl.txt"
RAW = ROOT / "raw"
WORKFLOWS = ROOT / "workflows"

# 24-char Mongo-style ids, as GHL uses throughout.
ID_RE = re.compile(r"[0-9a-fA-F]{24}")
DELAY = 0.4  # ~2.5 req/s — this is an internal endpoint, stay well-mannered


def parse_curl(text: str) -> tuple[str, dict[str, str]]:
    """Extract (url, headers) from a 'Copy as cURL' string."""
    text = text.replace("\\\n", " ").replace("^\n", " ").strip()
    try:
        tokens = shlex.split(text)
    except ValueError as e:
        raise SystemExit(f"Could not parse .session_curl.txt: {e}")

    url, headers = "", {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-H", "--header") and i + 1 < len(tokens):
            k, _, v = tokens[i + 1].partition(":")
            headers[k.strip()] = v.strip()
            i += 2
            continue
        if t in ("-b", "--cookie") and i + 1 < len(tokens):
            headers["Cookie"] = tokens[i + 1]
            i += 2
            continue
        if t.startswith("http"):
            url = t
        i += 1

    if not url:
        raise SystemExit("No URL found in .session_curl.txt — re-copy as cURL (bash).")
    if not any(k.lower() in ("authorization", "cookie", "token-id") for k in headers):
        print("WARNING: no auth header found. The capture may be incomplete.",
              file=sys.stderr)
    return url, headers


def load_workflow_ids() -> list[dict]:
    src = RAW / "workflows.json"
    if not src.exists():
        raise SystemExit("raw/workflows.json missing. Run ghl_pull.py first — the "
                         "workflow list is what this script iterates over.")
    data = json.loads(src.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("workflows", [])
    out = []
    for w in items:
        if isinstance(w, dict) and w.get("id"):
            out.append({"id": w["id"], "name": w.get("name", ""),
                        "status": w.get("status", "")})
    return out


def redact(text: str, headers: dict[str, str]) -> str:
    for v in headers.values():
        if v and len(v) > 20:
            text = text.replace(v, "***REDACTED***")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture GHL workflow internals")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N workflows")
    ap.add_argument("--published-only", action="store_true",
                    help="skip drafts — usually the right call for a migration audit")
    args = ap.parse_args()

    if not CURL_FILE.exists():
        print(f"ERROR: {CURL_FILE.name} not found.\n\n"
              "Capture it first:\n"
              "  1. Open a workflow in GHL\n"
              "  2. DevTools (F12) → Network → XHR\n"
              "  3. Find the response containing the step tree\n"
              "  4. Right-click → Copy → Copy as cURL (bash)\n"
              f"  5. Save into {CURL_FILE.name}\n", file=sys.stderr)
        return 2

    url, headers = parse_curl(CURL_FILE.read_text(encoding="utf-8"))
    workflows = load_workflow_ids()
    if args.published_only:
        workflows = [w for w in workflows if str(w.get("status", "")).lower() == "published"]

    # Prefer an id from the path over one in the query string: the query is
    # usually locationId, and substituting that would silently pull the same
    # workflow N times while looking like it worked.
    path_part, sep, query_part = url.partition("?")
    ids_in_path = ID_RE.findall(path_part)
    ids_anywhere = ID_RE.findall(url)
    if not ids_anywhere:
        print("ERROR: no 24-character id found in the captured URL, so there is "
              "nothing to substitute. Capture a request that targets one specific "
              "workflow.", file=sys.stderr)
        return 2

    if ids_in_path:
        target = ids_in_path[-1]
        template = path_part.replace(target, "{workflowId}") + sep + query_part
    else:
        target = ids_anywhere[-1]
        template = url.replace(target, "{workflowId}")
        print("WARNING: the workflow id appears only in the query string. Confirm "
              "the substituted id is the workflow, not the location.", file=sys.stderr)

    print(f"URL template : {template}")
    print(f"Headers      : {len(headers)} (values not shown)")
    print(f"Workflows    : {len(workflows)}"
          f"{' (published only)' if args.published_only else ''}")

    if args.dry_run:
        print("\nDry run — no requests made.")
        for w in workflows[:10]:
            print(f"  {w['id']}  {w['status']:<10} {w['name'][:60]}")
        if len(workflows) > 10:
            print(f"  ... and {len(workflows) - 10} more")
        return 0

    WORKFLOWS.mkdir(exist_ok=True)
    done = skipped = failed = 0

    for i, w in enumerate(workflows, 1):
        if args.limit and done >= args.limit:
            break
        dest = WORKFLOWS / f"{w['id']}.json"
        if dest.exists():
            skipped += 1
            continue

        req = request.Request(template.replace("{workflowId}", w["id"]), method="GET")
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            with request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode("utf-8") or "null")
            dest.write_text(json.dumps(
                {"_workflow": w, "_captured_from": template, "data": body},
                indent=2, default=str), encoding="utf-8")
            done += 1
            print(f"  [{i}/{len(workflows)}] ok   {w['name'][:56]}")
        except error.HTTPError as e:
            if e.code in (401, 403):
                print(f"\nSession expired after {done} workflow(s) "
                      f"({skipped} already on disk).\n"
                      "Re-capture .session_curl.txt from DevTools and run again — "
                      "completed workflows are skipped automatically.", file=sys.stderr)
                return 3
            failed += 1
            print(f"  [{i}/{len(workflows)}] FAIL {w['name'][:40]} "
                  f"{redact(str(e), headers)}")
        except Exception as e:  # noqa: BLE001 - one bad workflow must not end the run
            failed += 1
            print(f"  [{i}/{len(workflows)}] FAIL {w['name'][:40]} "
                  f"{redact(str(e), headers)}")

        time.sleep(DELAY)

    print(f"\ncaptured {done} · already had {skipped} · failed {failed}")
    print(f"Output: workflows/ ({len(list(WORKFLOWS.glob('*.json')))} file(s), gitignored)")
    if done:
        print("\nOpen one file and confirm the step tree is really in there before "
              "capturing the rest. A response that parses is not necessarily the "
              "response you wanted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
