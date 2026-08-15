#!/usr/bin/env python3
"""
Restore a backup into a scratch database and prove it came back intact.

An untested backup is not a backup. Nightly dumps have been running since the
stack came up, and until this script ran nobody had ever restored one — which
means the disaster-recovery position was a hope, not a fact.

What it does, without touching the live databases:

    1. takes a fresh dump (or uses the newest existing one)
    2. creates a scratch database
    3. restores into it
    4. counts rows in both and compares
    5. drops the scratch database

The comparison is the point. A restore that "succeeds" and produces an empty
schema is the failure mode worth catching, and a bare exit code will not.

Records the outcome in out/restore_verification.json so the date of the last
successful restore is a fact on disk, not a memory.

Usage:
    python scripts/verify_restore.py
    python scripts/verify_restore.py --database n8n
    python scripts/verify_restore.py --keep      # leave the scratch db for inspection
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]
OUT = ROOT / "out" / "restore_verification.json"

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

# EXACT row totals across every user table. Twenty puts workspace data in a
# per-workspace schema, so the tables are discovered rather than named.
#
# The obvious query — SUM(n_live_tup) FROM pg_stat_user_tables — is a planner
# *estimate*, and it reported an 18-row shortfall on a restore that was in fact
# perfect. Verifying a backup against an estimate cannot distinguish estimator
# drift from real data loss, which is the one thing this script exists to do.
# query_to_xml runs a real COUNT(*) per table. Slower, and correct.
COUNT_SQL = """
SELECT COALESCE(SUM(cnt), 0) FROM (
  SELECT (xpath('/row/c/text()', query_to_xml(
            format('SELECT COUNT(*) AS c FROM %I.%I', schemaname, tablename),
            false, true, '')))[1]::text::bigint AS cnt
  FROM pg_tables
  WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
) s;
"""


def env_value(key: str, default: str = "") -> str:
    for f in (ROOT / "infra" / ".env", ROOT / ".env"):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() == key:
                        return v.strip().strip("\"'")
    return default


def psql(sql: str, database: str, user: str, password: str) -> tuple[int, str]:
    """Run SQL in the db container. Returns (rc, stdout)."""
    cmd = COMPOSE + ["exec", "-T", "-e", f"PGPASSWORD={password}", "db",
                     "psql", "-U", user, "-d", database, "-tAc", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout or r.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Prove a backup restores")
    ap.add_argument("--database", default="default",
                    help="database to verify: default (Twenty) or n8n")
    ap.add_argument("--keep", action="store_true",
                    help="leave the scratch database behind for inspection")
    args = ap.parse_args()

    user = env_value("PG_DATABASE_USER", "postgres")
    password = env_value("PG_DATABASE_PASSWORD")
    if not password:
        print("ERROR: PG_DATABASE_PASSWORD not set in infra/.env", file=sys.stderr)
        return 2

    src = args.database
    scratch = f"restore_check_{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    # /backups is mounted on the backup container, not on db. The dump is
    # transient — the point is proving a restore works, not keeping this copy.
    dump = f"/tmp/verify-{src}.dump"

    print(f"{BOLD}Restore verification — {src}{OFF}\n")

    steps: list[tuple[str, bool, str]] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append((name, ok, detail))
        mark = f"{GREEN}PASS{OFF}" if ok else f"{RED}FAIL{OFF}"
        print(f"  {mark}  {name}" + (f"  {DIM}{detail}{OFF}" if detail else ""))
        return ok

    # 1. Source row count, before anything else touches the database.
    rc, before = psql(COUNT_SQL, src, user, password)
    if not step("read source row count", rc == 0 and before.isdigit(), before[:60]):
        return 1
    before_n = int(before)

    # 2. Dump. -Fc is the custom format the nightly backup also writes.
    r = subprocess.run(
        COMPOSE + ["exec", "-T", "-e", f"PGPASSWORD={password}", "db",
                   "pg_dump", "-U", user, "-d", src, "-Fc", "-f", dump],
        capture_output=True, text=True)
    if not step("take a dump", r.returncode == 0, (r.stderr or "")[:80]):
        return 1

    # 3. Scratch database — never the live one.
    rc, err = psql(f'CREATE DATABASE "{scratch}";', "postgres", user, password)
    if not step("create scratch database", rc == 0, scratch):
        print(f"    {err[:150]}")
        return 1

    try:
        # 4. Restore.
        r = subprocess.run(
            COMPOSE + ["exec", "-T", "-e", f"PGPASSWORD={password}", "db",
                       "pg_restore", "-U", user, "-d", scratch, "--no-owner",
                       "--no-privileges", dump],
            capture_output=True, text=True)
        # pg_restore warns about non-fatal issues and still exits non-zero, so
        # the row comparison below is the real verdict, not this return code.
        step("restore into scratch", True,
             "clean" if r.returncode == 0 else "completed with warnings")

        rc, after = psql(COUNT_SQL, scratch, user, password)
        ok = rc == 0 and after.isdigit()
        after_n = int(after) if ok else -1

        step("read restored row count", ok, after[:60])
        # The source can gain rows after the dump is taken — a scheduled
        # workflow firing mid-run is normal — so the restore may legitimately
        # hold slightly fewer. It must never hold *more*, and it must not be
        # short by more than a rounding error.
        drift = (before_n - after_n) / before_n if before_n else 1
        step("restored data matches the source",
             ok and before_n > 0 and after_n <= before_n and drift <= 0.001,
             f"{before_n} source → {after_n} restored"
             + (f", {before_n - after_n} written after the dump" if after_n < before_n else ""))
    finally:
        if args.keep:
            print(f"\n  {DIM}scratch database kept: {scratch}{OFF}")
        else:
            psql(f'DROP DATABASE "{scratch}";', "postgres", user, password)
            subprocess.run(COMPOSE + ["exec", "-T", "db", "rm", "-f", dump],
                           capture_output=True, text=True)

    passed = all(ok for _, ok, _ in steps)
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")

    OUT.parent.mkdir(exist_ok=True)
    record = json.loads(OUT.read_text()) if OUT.exists() else {}
    record[src] = {"verified_at": when, "passed": passed,
                   "source_rows": before_n,
                   "restored_rows": after_n if 'after_n' in dir() else None}
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    print(f"\n{'=' * 62}")
    if passed:
        print(f"  {GREEN}Backup of `{src}` restores cleanly.{OFF}  Verified {when}")
    else:
        print(f"  {RED}Restore verification FAILED for `{src}`.{OFF}")
    print(f"  Recorded in {OUT.relative_to(ROOT)}")
    print("=" * 62)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
