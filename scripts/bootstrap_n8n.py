#!/usr/bin/env python3
"""
Create the n8n owner account and a public API key, headlessly.

n8n's first run normally requires clicking through a setup wizard in the
browser, which makes the build unreproducible. This does the same over n8n's
internal REST API and writes the key into infra/.env, so the whole stack goes
from `up.sh` to deployed workflows with no manual step.

Endpoints used (n8n internal API, session-cookie authenticated):
    POST /rest/owner/setup   -> creates the owner, returns a session cookie
    POST /rest/login         -> session cookie when the owner already exists
    POST /rest/api-keys      -> the long-lived public API key

Usage:
    python scripts/bootstrap_n8n.py
    python scripts/bootstrap_n8n.py --email me@aspiretss.com --password '...'
"""

from __future__ import annotations

import argparse
import json
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "infra" / ".env"

DEFAULT_EMAIL = "admin@aspiretss.com"
DEFAULT_PASSWORD = "AspireDemo2026!"

# Everything the deploy script and the workflows need. Deliberately not the
# full admin scope — the key is stored on disk and used by automation.
SCOPES = [
    "workflow:create", "workflow:read", "workflow:update", "workflow:delete",
    "workflow:list", "workflow:activate", "workflow:deactivate",
    "credential:create", "credential:list",
    "execution:read", "execution:list",
]


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip("\"'")
    return env


def write_env(key: str, value: str) -> None:
    if not ENV_FILE.exists():
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and not line.strip().startswith("#"):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


class N8n:
    """
    n8n's internal API authenticates with an `n8n-auth` session cookie.

    The cookie is captured from the login/setup response and then sent as an
    explicit header. http.cookiejar will NOT send cookies back to a bare
    `localhost` host — the jar stores them, appears to work, and every
    subsequent call quietly 401s. Setting the header directly sidesteps that.
    """

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = request.build_opener(request.HTTPCookieProcessor(self.jar))
        self.token = ""

    def _headers(self, req: request.Request) -> None:
        req.add_header("Content-Type", "application/json")
        req.add_header("browser-id", "aspire-bootstrap")
        if self.token:
            req.add_header("Cookie", f"n8n-auth={self.token}")

    def _capture_token(self) -> None:
        for c in self.jar:
            if c.name == "n8n-auth":
                self.token = c.value

    def post(self, path: str, body: dict) -> tuple[dict | None, str]:
        req = request.Request(f"{self.base}{path}",
                              data=json.dumps(body).encode(), method="POST")
        self._headers(req)
        try:
            with self.opener.open(req, timeout=45) as r:
                payload = json.loads(r.read().decode() or "{}")
            self._capture_token()
            return payload, ""
        except error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
        except Exception as e:  # noqa: BLE001
            return None, str(e)

    def delete(self, path: str) -> tuple[dict | None, str]:
        req = request.Request(f"{self.base}{path}", method="DELETE")
        self._headers(req)
        try:
            with self.opener.open(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}"), ""
        except error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
        except Exception as e:  # noqa: BLE001
            return None, str(e)

    def get(self, path: str) -> tuple[dict | None, str]:
        req = request.Request(f"{self.base}{path}", method="GET")
        self._headers(req)
        try:
            with self.opener.open(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}"), ""
        except error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
        except Exception as e:  # noqa: BLE001
            return None, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap n8n owner and API key")
    ap.add_argument("--email", default=DEFAULT_EMAIL)
    ap.add_argument("--password", default=DEFAULT_PASSWORD)
    ap.add_argument("--first-name", default="Rafi")
    ap.add_argument("--last-name", default="Ahmed")
    args = ap.parse_args()

    env = read_env()
    base = env.get("N8N_BASE_URL") or env.get("N8N_PUBLIC_URL") or "http://localhost:5678"
    api = N8n(base)

    print(f"Bootstrapping {base}\n")

    settings, err = api.get("/rest/settings")
    if err:
        print(f"  FAIL  cannot reach n8n — {err}", file=sys.stderr)
        return 1
    needs_setup = (settings.get("data", {})
                   .get("userManagement", {})
                   .get("showSetupOnFirstLoad", False))

    if needs_setup:
        _, err = api.post("/rest/owner/setup", {
            "email": args.email, "password": args.password,
            "firstName": args.first_name, "lastName": args.last_name})
        if err:
            print(f"  FAIL  owner setup: {err}", file=sys.stderr)
            return 1
        print(f"  ok    owner created            {args.email}")
    else:
        _, err = api.post("/rest/login",
                          {"emailOrLdapLoginId": args.email,
                           "password": args.password})
        if err:
            print(f"  FAIL  login: {err}\n"
                  "        The owner exists with different credentials. Pass "
                  "--email/--password, or reset with `up.sh --destroy`.",
                  file=sys.stderr)
            return 1
        print(f"  ok    owner exists, signed in  {args.email}")

    # n8n returns the raw key only at creation and rejects a duplicate label,
    # so a re-run must delete the old one. The key lives in infra/.env; there is
    # nothing to recover from the old record.
    LABEL = "aspire-automation"
    existing, _ = api.get("/rest/api-keys")
    # The list is under data.items, not data.
    for k in (existing or {}).get("data", {}).get("items", []):
        if isinstance(k, dict) and k.get("label") == LABEL and k.get("id"):
            api.delete(f"/rest/api-keys/{k['id']}")
            print(f"  ok    replaced previous key    {LABEL}")

    created, err = api.post("/rest/api-keys", {
        "label": LABEL, "expiresAt": None, "scopes": SCOPES})
    if err or not created:
        print(f"  FAIL  api key: {err}", file=sys.stderr)
        return 1

    raw = created.get("data", {}).get("rawApiKey", "")
    if not raw:
        print("  FAIL  api key created but no token returned", file=sys.stderr)
        return 1

    write_env("N8N_API_KEY", raw)
    print("  ok    API key                 written to infra/.env")
    print(f"\n  n8n      {base}")
    print(f"  login    {args.email} / {args.password}")
    print("\n  Next: python scripts/n8n_deploy.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
