#!/usr/bin/env python3
"""
Create the n8n credentials the workflow library expects, from infra/.env.

Previously a manual click-through in the n8n UI, which is why the workflows sat
deployed but unproven for so long: nothing could run without credentials, and
nobody could create them without a browser.

Two credentials, both referenced by *name* from the generated workflows
(`n8n_deploy.py` binds the environment's ids at deploy time):

    Twenty API   httpHeaderAuth   Authorization: Bearer <TWENTY_API_KEY>
    Aspire SMTP  smtp             host/port/user/password from infra/.env

The public API has no "update credential" verb that preserves the id, so a
credential whose name already exists is deleted and recreated. That is safe:
the values here are derived from .env, not entered by hand, so nothing is lost.

Usage:
    python scripts/n8n_credentials.py
    python scripts/n8n_credentials.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent

CRED_TWENTY = "Twenty API"
CRED_SMTP = "Aspire SMTP"


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for candidate in (ROOT / "infra" / ".env", ROOT / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    for k in list(env) + ["N8N_BASE_URL", "N8N_API_KEY", "TWENTY_API_KEY"]:
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


class N8n:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/") + "/api/v1"
        self.key = key

    def _call(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("X-N8N-API-KEY", self.key)
        req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "{}"), ""
        except error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            return e.code, None, f"HTTP {e.code}: {redact(body, self.key)}"
        except Exception as e:  # noqa: BLE001
            return 0, None, redact(str(e), self.key)


def redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s and len(s) > 6:
            text = text.replace(s, "***")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision n8n credentials")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = read_env()
    base = env.get("N8N_BASE_URL", "http://localhost:5678")
    n8n_key = env.get("N8N_API_KEY", "")
    twenty_key = env.get("TWENTY_API_KEY", "")

    if not n8n_key:
        print("ERROR: N8N_API_KEY not set. Run scripts/bootstrap_n8n.py first.",
              file=sys.stderr)
        return 2
    if not twenty_key:
        print("ERROR: TWENTY_API_KEY not set. Run scripts/bootstrap_workspace.py "
              "first.", file=sys.stderr)
        return 2

    # Defaults point at the Mailpit container, so a freshly built stack can
    # prove the send path with no external mail account at all. Set the
    # EMAIL_SMTP_* vars in infra/.env to send for real.
    smtp = {
        "host": env.get("EMAIL_SMTP_HOST") or "mailpit",
        "port": int(env.get("EMAIL_SMTP_PORT") or 1025),
        "user": env.get("EMAIL_SMTP_USER") or "aspire",
        "password": env.get("EMAIL_SMTP_PASSWORD") or "aspire",
        "secure": (env.get("EMAIL_SMTP_SECURE", "") or "false").lower() == "true",
    }
    # Mailpit speaks plaintext SMTP; STARTTLS must be off or the node hangs
    # on the handshake and fails with a bare timeout.
    if smtp["host"] in ("mailpit", "localhost", "127.0.0.1"):
        smtp["disableStartTls"] = True

    wanted = [
        (CRED_TWENTY, "httpHeaderAuth",
         {"name": "Authorization", "value": f"Bearer {twenty_key}"}),
        (CRED_SMTP, "smtp", smtp),
    ]

    print(f"n8n {base}\n")
    if args.dry_run:
        for name, ctype, data in wanted:
            shown = {k: ("***" if k in ("password", "value") else v)
                     for k, v in data.items()}
            print(f"  {name:14} {ctype:16} {shown}")
        print("\nDry run — nothing sent.")
        return 0

    api = N8n(base, n8n_key)

    # The public API exposes no credential *list*; the internal one does, but
    # DELETE by id needs an id we do not have. Creating a duplicate name is
    # allowed by n8n and would leave the deploy binding an arbitrary one of
    # them, so names are made unique by deleting on conflict — see below.
    status, existing, err = api._call("GET", "/credentials?limit=250")
    known: dict[str, str] = {}
    if status == 200 and existing:
        for c in existing.get("data", []):
            if isinstance(c, dict) and c.get("name"):
                known[c["name"]] = c["id"]

    failures = 0
    for name, ctype, data in wanted:
        if name in known:
            api._call("DELETE", f"/credentials/{known[name]}")
        status, body, err = api._call("POST", "/credentials", {
            "name": name, "type": ctype, "data": data})
        if err or status >= 400:
            failures += 1
            print(f"  FAIL  {name:14} {err[:160]}")
            continue
        verb = "replaced" if name in known else "created"
        print(f"  ok    {name:14} {ctype:16} {verb}")

    if failures:
        print(f"\n{failures} credential(s) failed.", file=sys.stderr)
        return 1

    print(f"\n  SMTP -> {smtp['host']}:{smtp['port']}"
          + ("   (Mailpit — nothing leaves this host)"
             if smtp["host"] == "mailpit" else ""))
    print("\n  Next: python scripts/n8n_deploy.py --dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
