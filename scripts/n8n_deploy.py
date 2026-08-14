#!/usr/bin/env python3
"""
Push the generated workflow library into n8n via its REST API.

Idempotent: a workflow whose name already exists is updated, not duplicated.
Safe to run on every change — that is the point, since the source of truth is
n8n/workflows/*.json in Git, not whatever is currently in the n8n database.

Order matters. SYS Error Handler and VEND Send Email are deployed first
because the others reference them by name.

Usage:
    python scripts/n8n_deploy.py --dry-run
    python scripts/n8n_deploy.py
    python scripts/n8n_deploy.py --activate      # also switch them on
    python scripts/n8n_deploy.py --only "SYS Error Handler"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "n8n" / "workflows"

# Dependency order: these two are referenced by name from the others.
FIRST = ["SYS Error Handler", "VEND Send Email"]

# n8n rejects extra top-level keys on create; only these are accepted.
ALLOWED = {"name", "nodes", "connections", "settings", "staticData"}


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for candidate in (ROOT / ".env", ROOT / "infra" / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip("\"'")
    for k in ("N8N_BASE_URL", "N8N_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


class N8n:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.key = key

    def _call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(f"{self.base}/api/v1{path}", data=data, method=method)
        req.add_header("X-N8N-API-KEY", self.key)
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=45) as resp:
                return resp.status, json.loads(resp.read().decode() or "null"), ""
        except error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400].replace(self.key, "***")
            return e.code, None, f"HTTP {e.code}: {detail}"
        except Exception as e:  # noqa: BLE001
            return 0, None, str(e).replace(self.key, "***")

    def credentials(self) -> dict[str, str]:
        """Map credential name -> id. Generated workflows reference credentials
        by name so the JSON stays portable across environments; the ids are
        environment-specific and are bound here at deploy time."""
        status, body, err = self._call("GET", "/credentials?limit=250")
        if err or status >= 400:
            # Some n8n versions do not expose credential listing on the public
            # API. Not fatal — n8n resolves many by name, and anything that
            # fails to resolve is visible immediately in the editor.
            return {}
        items = (body or {}).get("data", body) or []
        return {c["name"]: c["id"] for c in items
                if isinstance(c, dict) and c.get("name") and c.get("id")}

    def existing(self) -> dict[str, str]:
        """Map workflow name → id for everything already in n8n."""
        status, body, err = self._call("GET", "/workflows?limit=250")
        if err or status >= 400:
            sys.exit(f"Cannot reach n8n — {err or status}\n"
                     "Check N8N_BASE_URL and that the API key is valid "
                     "(n8n → Settings → n8n API → Create an API key).")
        items = (body or {}).get("data", body) or []
        return {w["name"]: w["id"] for w in items if isinstance(w, dict)}

    def create(self, payload: dict):
        return self._call("POST", "/workflows", payload)

    def update(self, wid: str, payload: dict):
        return self._call("PUT", f"/workflows/{wid}", payload)

    def activate(self, wid: str):
        return self._call("POST", f"/workflows/{wid}/activate")


def bind_credentials(flow: dict, by_name: dict[str, str]) -> int:
    """Inject the environment's credential ids into every node that names one."""
    bound = 0
    for node in flow.get("nodes", []):
        for cred_type, ref in (node.get("credentials") or {}).items():
            name = ref.get("name") if isinstance(ref, dict) else None
            if name and name in by_name:
                node["credentials"][cred_type] = {"id": by_name[name], "name": name}
                bound += 1
    return bound


def load_workflows(only: str) -> list[dict]:
    if not WORKFLOWS.exists():
        sys.exit(f"{WORKFLOWS} missing. Run scripts/build_n8n_workflows.py first.")
    flows = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(WORKFLOWS.glob("*.json"))]
    if only:
        wanted = {s.strip() for s in only.split(",") if s.strip()}
        flows = [f for f in flows if f["name"] in wanted]
    # Dependencies first, then everything else alphabetically.
    return sorted(flows, key=lambda f: (FIRST.index(f["name"])
                                        if f["name"] in FIRST else len(FIRST),
                                        f["name"]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy workflows to n8n")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--activate", action="store_true",
                    help="activate after deploying (leave off until tested)")
    ap.add_argument("--only", default="", help="comma-separated workflow names")
    args = ap.parse_args()

    flows = load_workflows(args.only)
    print(f"{len(flows)} workflow(s) to deploy\n")

    if args.dry_run:
        for f in flows:
            print(f"  {f['name']:32} {len(f['nodes']):>2} nodes")
        print("\nDry run — nothing sent.")
        return 0

    env = read_env()
    base = env.get("N8N_BASE_URL", "http://localhost:5678")
    key = env.get("N8N_API_KEY", "")
    if not key:
        print("ERROR: N8N_API_KEY not set. In n8n: Settings → n8n API → "
              "Create an API key, then add it to .env", file=sys.stderr)
        return 2

    api = N8n(base, key)
    present = api.existing()
    creds = api.credentials()
    print(f"Connected to {base} — {len(present)} workflow(s) already there")
    print(f"Credentials available: {', '.join(sorted(creds)) or 'none found'}\n")

    missing = {name
               for f in flows for n in f.get("nodes", [])
               for ref in (n.get("credentials") or {}).values()
               if (name := ref.get("name") if isinstance(ref, dict) else None)
               and name not in creds}
    if missing:
        print(f"  !!    not yet created in n8n: {', '.join(sorted(missing))}")
        print("        Those nodes will fail until the credential exists.\n")

    failures = 0
    for f in flows:
        bind_credentials(f, creds)
        payload = {k: v for k, v in f.items() if k in ALLOWED}
        name = f["name"]

        if name in present:
            wid = present[name]
            status, _, err = api.update(wid, payload)
            verb = "updated"
        else:
            status, body, err = api.create(payload)
            wid = (body or {}).get("id", "")
            verb = "created"

        if err or status >= 400:
            failures += 1
            print(f"  FAIL  {name:32} {err[:150]}")
            continue

        note = ""
        if args.activate and wid:
            astatus, _, aerr = api.activate(wid)
            note = " + activated" if not aerr and astatus < 400 else f" (activate failed: {aerr[:60]})"
        print(f"  ok    {name:32} {verb}{note}")

    print(f"\n{len(flows) - failures}/{len(flows)} deployed.")
    if not args.activate:
        print("Workflows are INACTIVE. Test each one in the n8n editor, then "
              "re-run with --activate.")
    if failures:
        print("\nFailures carry n8n's own error text. Node type versions differ "
              "between n8n releases — send me the error and the generator gets "
              "one edit.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
