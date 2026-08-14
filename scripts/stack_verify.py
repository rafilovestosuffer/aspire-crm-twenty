#!/usr/bin/env python3
"""
Verify the whole Aspire stack is actually working.

Checks each layer separately rather than inferring health from one service
being up. The failure this exists to catch: Twenty's UI looks perfectly fine
while the worker container is dead, and every scheduled workflow and mailbox
sync silently stops.

Usage:
    python scripts/stack_verify.py
    python scripts/stack_verify.py --verbose
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]

OK, WARN, FAIL = "ok", "warn", "FAIL"
GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, state: str, detail: str = "") -> None:
        colour = {OK: GREEN, WARN: YELLOW, FAIL: RED}[state]
        print(f"  {colour}{state:>4}{RESET}  {name:<34} {DIM}{detail}{RESET}")
        self.rows.append((name, state, detail))

    @property
    def failures(self) -> int:
        return sum(1 for _, s, _ in self.rows if s == FAIL)

    @property
    def warnings(self) -> int:
        return sum(1 for _, s, _ in self.rows if s == WARN)


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for candidate in (ROOT / "infra" / ".env", ROOT / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    return env


def sh(args: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def http(url: str, headers: dict[str, str] | None = None, timeout: int = 15):
    req = request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), ""
    except error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500], ""
    except Exception as e:  # noqa: BLE001
        return 0, "", str(e)


def check_containers(rep: Report, verbose: bool) -> dict[str, str]:
    code, out = sh(COMPOSE + ["ps", "--format", "json"])
    if code != 0:
        rep.add("docker compose", FAIL, "cannot run — is Docker running?")
        return {}

    services: dict[str, str] = {}
    raw = out.strip()
    if raw:
        try:
            blob = json.loads(raw if raw.startswith("[")
                              else "[" + ",".join(raw.splitlines()) + "]")
            for c in blob:
                services[c.get("Service", "?")] = (
                    c.get("Health") or c.get("State") or "?")
        except json.JSONDecodeError:
            pass

    # Every service that must be running for the stack to work.
    for name, why in [
        ("db", "Postgres"),
        ("redis", "Redis"),
        ("server", "Twenty API + UI"),
        ("worker", "scheduled workflows, mailbox sync"),
        ("n8n", "automation"),
    ]:
        state = services.get(name)
        if state is None:
            rep.add(f"container: {name}", FAIL, f"not running — {why}")
        elif state in ("healthy", "running"):
            rep.add(f"container: {name}", OK, state if verbose else why)
        elif state == "starting":
            rep.add(f"container: {name}", WARN, "still starting")
        else:
            rep.add(f"container: {name}", FAIL, state)
    return services


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the Aspire stack")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    env = read_env()
    rep = Report()

    print(f"\n{DIM}Containers{RESET}")
    check_containers(rep, args.verbose)

    print(f"\n{DIM}Data layer{RESET}")
    code, out = sh(COMPOSE + ["exec", "-T", "db", "pg_isready", "-U",
                              env.get("PG_DATABASE_USER", "postgres")])
    rep.add("postgres accepting connections", OK if code == 0 else FAIL, out.strip()[:60])

    code, out = sh(COMPOSE + ["exec", "-T", "db", "psql", "-U",
                              env.get("PG_DATABASE_USER", "postgres"), "-lqt"])
    if code == 0:
        names = {l.split("|")[0].strip() for l in out.splitlines() if "|" in l}
        for db, who in (("default", "Twenty"), ("n8n", "n8n")):
            rep.add(f"database: {db}", OK if db in names else FAIL, who)
    else:
        rep.add("database list", FAIL, out.strip()[:60])

    code, out = sh(COMPOSE + ["exec", "-T", "redis", "redis-cli", "ping"])
    rep.add("redis", OK if "PONG" in out else FAIL, out.strip()[:40])

    print(f"\n{DIM}Twenty{RESET}")
    base = env.get("TWENTY_BASE_URL") or env.get("SERVER_URL") or "http://localhost:3000"
    status, _, err = http(f"{base.rstrip('/')}/healthz")
    rep.add("twenty /healthz", OK if status == 200 else FAIL, err or f"HTTP {status}")

    key = env.get("TWENTY_API_KEY", "")
    if not key:
        rep.add("twenty REST API", WARN,
                "TWENTY_API_KEY not set — Settings → API & Webhooks")
    else:
        # NOTE: the metadata endpoint rejects any query string —
        # "Metadata path 'objects?limit=1' does not exist" — so no paging args.
        status, body, err = http(f"{base.rstrip('/')}/rest/metadata/objects",
                                 {"Authorization": f"Bearer {key}"})
        if status == 200:
            try:
                node = json.loads(body)
                for k in ("data", "objects"):
                    if isinstance(node, dict) and k in node:
                        node = node[k]
                objs = node if isinstance(node, list) else []
            except json.JSONDecodeError:
                objs = []
            names = {o.get("nameSingular") for o in objs if isinstance(o, dict)}
            custom = sum(1 for o in objs if isinstance(o, dict) and o.get("isCustom"))
            aspire = sum(1 for o in ("serviceSubscription", "complianceEngagement",
                                     "trainingAccount", "phishingBaseline")
                         if o in names)
            rep.add("twenty REST API", OK, f"{len(objs)} objects ({custom} custom)")
            rep.add("aspire custom objects", OK if aspire == 4 else WARN,
                    "all present" if aspire == 4
                    else f"{aspire}/4 — run scripts/twenty_provision.py")
        elif status in (401, 403):
            rep.add("twenty REST API", FAIL, "key rejected")
        else:
            rep.add("twenty REST API", FAIL, err or f"HTTP {status}")

    print(f"\n{DIM}n8n{RESET}")
    n8n = env.get("N8N_BASE_URL") or env.get("N8N_PUBLIC_URL") or "http://localhost:5678"
    status, _, err = http(f"{n8n.rstrip('/')}/healthz")
    rep.add("n8n /healthz", OK if status == 200 else FAIL, err or f"HTTP {status}")

    nkey = env.get("N8N_API_KEY", "")
    if not nkey:
        rep.add("n8n API", WARN, "N8N_API_KEY not set — Settings → n8n API")
    else:
        status, body, err = http(f"{n8n.rstrip('/')}/api/v1/workflows?limit=100",
                                 {"X-N8N-API-KEY": nkey})
        if status == 200:
            try:
                items = json.loads(body).get("data", [])
                active = sum(1 for w in items if w.get("active"))
                rep.add("n8n API", OK, f"{len(items)} workflow(s), {active} active")
            except json.JSONDecodeError:
                rep.add("n8n API", OK, "reachable")
        else:
            rep.add("n8n API", FAIL, err or f"HTTP {status}")

    print(f"\n{DIM}Backups{RESET}")
    backups = sorted((ROOT / "infra" / "backups").glob("*.dump"))
    if backups:
        newest = max(backups, key=lambda p: p.stat().st_mtime)
        rep.add("backup files", OK, f"{len(backups)}, newest {newest.name}")
    else:
        rep.add("backup files", WARN, "none yet — the first runs 24h after start")

    print()
    if rep.failures:
        print(f"{RED}{rep.failures} failure(s){RESET}, {rep.warnings} warning(s)\n")
        print("Common causes:")
        print("  worker not running   docker compose -f infra/docker-compose.yml logs worker")
        print("  server unhealthy     ... logs server   (first boot runs migrations)")
        print("  key rejected         regenerate in Twenty and update infra/.env")
        return 1

    print(f"{GREEN}Stack healthy{RESET}" + (f", {rep.warnings} warning(s)" if rep.warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
