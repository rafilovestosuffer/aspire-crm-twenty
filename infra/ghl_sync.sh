#!/usr/bin/env bash
# Recurring GHL -> Twenty contact/company/opportunity sync.
#
#   0 */4 * * * /home/rafi/aspire-crm-twenty/infra/ghl_sync.sh
#
# Keeps the continuity fallback actually current. Without this, Twenty's CRM
# data is only as fresh as the last time someone remembered to run
# scripts/ghl_contact_sync.py by hand -- see docs/09-status-and-handover.md.
# Every 4 hours is a starting point, not a mandate: GHL's rate ceiling
# (100 req / 10 s, 200,000 / day) leaves room for hourly if the business
# wants fresher data. Cron has a thin PATH -- we put the usual locations on it.

set -uo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

env_get() {
  grep -E "^$1=" "$HERE/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"''
}

hook="$(env_get ALERT_WEBHOOK_URL)"
host="$(hostname -f 2>/dev/null || hostname)"
log="/tmp/aspire-ghl-sync.log"

if ! python3 scripts/ghl_contact_sync.py >"$log" 2>&1; then
  # Errors from the sync script never contain names/emails/phones -- only GHL
  # record ids and HTTP status text -- so this alert body is safe to send.
  body=$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' \
    "Aspire GHL sync FAILED on ${host}. Last lines: $(tail -c 700 "$log")")
  if [[ -n "$hook" && "$hook" != *alert-sink* ]]; then
    curl -fsS -X POST -H 'Content-Type: application/json' -d "$body" "$hook" \
      >/dev/null 2>&1 || true
  fi
  cat "$log" >&2
  exit 1
fi
exit 0
