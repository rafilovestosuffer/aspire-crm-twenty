#!/usr/bin/env python3
"""
Create the Twenty workspace, admin user and API key without touching the UI.

Twenty's onboarding is normally a browser flow. That makes the build
unreproducible: every rebuild needs someone to click through signup and copy a
key by hand. This does the same thing over GraphQL and writes the key straight
into infra/.env, so `up.sh` → `bootstrap_workspace.py` → provision → seed runs
unattended.

The auth mutations live on /metadata, not /graphql, and introspection is
disabled in the shipped image — the shapes below were read out of the compiled
DTOs in the container:

    signUp(email, password)            -> AvailableWorkspacesAndAccessTokens
    signUpInNewWorkspace               -> SignUp { loginToken, workspace }
    getAuthTokensFromLoginToken(...)   -> AuthTokens { tokens }
    createApiKey / core API            -> the long-lived key

Usage:
    python scripts/bootstrap_workspace.py
    python scripts/bootstrap_workspace.py --email me@aspiretss.com --password '...'
    python scripts/bootstrap_workspace.py --show      # print what exists, change nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "infra" / ".env"

DEFAULT_EMAIL = "admin@aspiretss.com"
DEFAULT_PASSWORD = "AspireDemo2026!"


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
    """Set one key in infra/.env, preserving comments and order."""
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


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def gql(self, query: str, token: str | None = None) -> tuple[dict | None, str]:
        body = json.dumps({"query": query}).encode()
        req = request.Request(f"{self.base}/metadata", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with request.urlopen(req, timeout=45) as r:
                payload = json.loads(r.read().decode() or "{}")
        except error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
        except Exception as e:  # noqa: BLE001
            return None, str(e)
        if payload.get("errors"):
            return None, payload["errors"][0].get("message", "unknown GraphQL error")
        return payload.get("data"), ""

    def rest(self, method: str, path: str, token: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode() or "null"), ""
        except error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:250]}"
        except Exception as e:  # noqa: BLE001
            return None, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap the Twenty workspace")
    ap.add_argument("--email", default=DEFAULT_EMAIL)
    ap.add_argument("--password", default=DEFAULT_PASSWORD)
    ap.add_argument("--workspace", default="Aspire Tech")
    ap.add_argument("--show", action="store_true", help="report state, change nothing")
    args = ap.parse_args()

    env = read_env()
    base = env.get("TWENTY_BASE_URL") or env.get("SERVER_URL") or "http://localhost:3000"
    api = Api(base)
    origin = base

    if args.show:
        key = env.get("TWENTY_API_KEY", "")
        print(f"instance     {base}")
        print(f"TWENTY_API_KEY  {'set' if key else 'NOT SET'}")
        if key:
            data, err = api.rest("GET", "/rest/metadata/objects?limit=200", key)
            print(f"objects      {json.dumps(data)[:60] if data else err}")
        return 0

    print(f"Bootstrapping {base}\n")

    # ---- 1. user -----------------------------------------------------------
    q = (f'mutation{{signUp(email:"{args.email}",password:"{args.password}")'
         '{tokens{accessOrWorkspaceAgnosticToken{token}}}}')
    data, err = api.gql(q)
    if data:
        agnostic = data["signUp"]["tokens"]["accessOrWorkspaceAgnosticToken"]["token"]
        print(f"  ok    user created            {args.email}")
    else:
        # Already exists: sign in instead. Makes the script safe to re-run.
        q = (f'mutation{{signIn(email:"{args.email}",password:"{args.password}")'
             '{tokens{accessOrWorkspaceAgnosticToken{token}}}}')
        data, err2 = api.gql(q)
        if not data:
            print(f"  FAIL  signup: {err}\n        signin: {err2}", file=sys.stderr)
            return 1
        agnostic = data["signIn"]["tokens"]["accessOrWorkspaceAgnosticToken"]["token"]
        print(f"  ok    user exists, signed in  {args.email}")

    # ---- 2. workspace ------------------------------------------------------
    # On a fresh instance this creates one. On a re-run it is refused with
    # "New workspace setup is disabled", which simply means one already exists.
    data, err = api.gql(
        "mutation{signUpInNewWorkspace{loginToken{token}workspace{id}}}",
        agnostic)
    if data:
        print(f"  ok    workspace created       {data['signUpInNewWorkspace']['workspace']['id']}")
    elif "disabled" in err.lower() or "already" in err.lower():
        print("  ok    workspace already exists")
    else:
        print(f"  FAIL  workspace: {err}", file=sys.stderr)
        return 1

    # ---- 3. workspace-scoped access token ----------------------------------
    # The token from step 1 is workspace-agnostic and cannot touch workspace
    # data. Exchanging credentials against the origin yields a scoped one.
    data, err = api.gql(
        f'mutation{{getLoginTokenFromCredentials(email:"{args.email}",'
        f'password:"{args.password}",origin:"{origin}"){{loginToken{{token}}}}}}')
    if not data:
        print(f"  FAIL  login token: {err}", file=sys.stderr)
        return 1
    login_token = data["getLoginTokenFromCredentials"]["loginToken"]["token"]

    data, err = api.gql(
        f'mutation{{getAuthTokensFromLoginToken(loginToken:"{login_token}",'
        f'origin:"{origin}"){{tokens{{accessOrWorkspaceAgnosticToken{{token}}}}}}}}')
    if not data:
        print(f"  FAIL  access token: {err}", file=sys.stderr)
        return 1
    access = data["getAuthTokensFromLoginToken"]["tokens"][
        "accessOrWorkspaceAgnosticToken"]["token"]
    print("  ok    access token            workspace-scoped")

    # ---- 4. activate the workspace ------------------------------------------
    # A new workspace sits in PENDING_CREATION and has no data source. Until it
    # is activated every core REST call fails with "No data sources found".
    data, err = api.gql(
        f'mutation{{activateWorkspace(data:{{displayName:"{args.workspace}"}})'
        '{id}}', access)
    if data:
        print(f"  ok    workspace activated     {args.workspace}")
    elif "not pending" in err.lower() or "active" in err.lower():
        print("  ok    workspace already active")
    else:
        print(f"  !!    activation: {err[:90]}")

    # ---- 5. long-lived API key ---------------------------------------------
    # The short-lived access token expires in minutes; scripts need a real key.
    api_key = ""

    # An API key must be bound to a role or creation fails with "Could not find
    # role". Admin is what the provisioning and seeding scripts need — they
    # write metadata, which Member cannot do.
    roles, rerr = api.gql("{getRoles{id label}}", access)
    role_id = ""
    if roles:
        for r in roles.get("getRoles", []):
            if r.get("label") == "Admin":
                role_id = r["id"]
                break

    created, err = api.rest("POST", "/rest/apiKeys", access,
                            {"name": "aspire-automation",
                             "expiresAt": "2030-01-01T00:00:00.000Z",
                             **({"roleId": role_id} if role_id else {})})
    if created:
        # /rest/apiKeys returns the record directly, not wrapped in "data".
        node = created.get("data", created) if isinstance(created, dict) else {}
        rec = node.get("createApiKey") or node.get("apiKey") or node
        key_id = rec.get("id") if isinstance(rec, dict) else None
        if key_id:
            tok, terr = api.gql(
                f'mutation{{generateApiKeyToken(apiKeyId:"{key_id}",'
                f'expiresAt:"2030-01-01T00:00:00.000Z"){{token}}}}', access)
            if tok:
                api_key = tok["generateApiKeyToken"]["token"]
            else:
                err = terr

    if api_key:
        write_env("TWENTY_API_KEY", api_key)
        print(f"  ok    API key                 written to infra/.env")
    else:
        # Not fatal — the access token proves the workspace works, and a key can
        # be made in the UI in ten seconds.
        print(f"  !!    API key not created automatically ({err[:80]})")
        print("        Create one: Settings → API & Webhooks → Create key,")
        print("        then set TWENTY_API_KEY in infra/.env")

    print(f"\n  Twenty   {base}")
    print(f"  login    {args.email} / {args.password}")
    print("\n  Next: python scripts/twenty_provision.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
