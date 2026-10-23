#!/usr/bin/env bash
# Daily one-way copy GHL -> Twenty while both systems run side by side.
#
#   30 2 * * * /home/rafi/aspire-crm-twenty/infra/ghl-sync.sh
#
# Export (GET-only, scripts/ghl_export_records.py), then import
# (scripts/ghl_import_records.py, matched on GHL ids, so a re-run creates only
# what is new and updates only what GHL changed). The import runs only when the
# export matched GHL's own totals: a short read must not reach Twenty looking
# complete.
#
# Failures POST to ALERT_WEBHOOK_URL from infra/.env, as watchdog.sh does. The
# alert names the step, never log lines: the log holds contact data and lives
# in raw/ (gitignored, mode 700) for the same reason.

set -uo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

# Same precedence as ghl_import_records.py: repo-root .env first, then infra/.env.
env_get() {
  local f v
  for f in "$ROOT/.env" "$HERE/.env"; do
    # \r too: an .env saved on Windows otherwise puts a CR inside the
    # Authorization header, and the resulting ValueError prints the token.
    v=$(grep -E "^$1=" "$f" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'\r")
    if [[ -n "$v" ]]; then printf '%s' "$v"; return; fi
  done
}

hook="$(env_get ALERT_WEBHOOK_URL)"
host="$(hostname -f 2>/dev/null || hostname)"

fail() {
  echo "ghl-sync FAILED: $1" >&2
  if [[ -n "$hook" && "$hook" != *alert-sink* ]]; then
    body=$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' \
      "Aspire GHL->Twenty sync FAILED on ${host}: $1. Log: ${ROOT}/raw/ghl_sync.log")
    curl -fsS -X POST -H 'Content-Type: application/json' -d "$body" "$hook" \
      >/dev/null 2>&1 || true
  fi
  exit 1
}

# One run at a time. A slow export must not overlap tomorrow's.
exec 9>"/tmp/aspire-ghl-sync.lock"
if ! flock -n 9; then
  echo "ghl-sync: previous run still going, skipping" >&2
  exit 0
fi

for k in GHL_TOKEN GHL_LOCATION TWENTY_BASE_URL TWENTY_API_KEY; do
  [[ -n "$(env_get "$k")" ]] || fail "$k is not set in .env or infra/.env (on this host TWENTY_BASE_URL is http://127.0.0.1:3000)"
done
# The export reads only the repo-root .env and the environment; this lets the
# token live in infra/.env with the rest of the server's secrets.
GHL_TOKEN="$(env_get GHL_TOKEN)"; GHL_LOCATION="$(env_get GHL_LOCATION)"
export GHL_TOKEN GHL_LOCATION

python3 -c 'import phonenumbers' 2>/dev/null \
  || fail "python3 module phonenumbers is missing (pip install phonenumbers)"

umask 077
mkdir -p "$ROOT/raw"
chmod 700 "$ROOT/raw"
log="$ROOT/raw/ghl_sync.log"
{ echo "== ghl-sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"; } >"$log"

python3 scripts/ghl_export_records.py >>"$log" 2>&1 \
  || fail "export did not complete or did not match GHL totals; Twenty left unchanged"
python3 scripts/ghl_import_records.py >>"$log" 2>&1 \
  || fail "import reported failures (details in out/ghl_import_report.json)"

date -u +%Y-%m-%dT%H:%M:%SZ >"$ROOT/out/ghl_sync_last_ok.txt"
echo "ghl-sync ok"
exit 0
