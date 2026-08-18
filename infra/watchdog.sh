#!/usr/bin/env bash
# Hourly check that the stack is still actually working.
#
#   0 * * * * /home/rafi/aspire-crm-twenty/infra/watchdog.sh
#
# stack_verify.py is the same gate deploy.sh uses. A dead Twenty worker looks
# like a healthy UI; this is how a person finds out. Failures POST to
# ALERT_WEBHOOK_URL from infra/.env. Cron has a thin PATH — we put the usual
# locations on it.

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
log="/tmp/aspire-stack-watchdog.log"

if ! python3 scripts/stack_verify.py >"$log" 2>&1; then
  body=$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' \
    "Aspire CRM watchdog FAILED on ${host}. Last lines: $(tail -c 700 "$log")")
  if [[ -n "$hook" && "$hook" != *alert-sink* ]]; then
    curl -fsS -X POST -H 'Content-Type: application/json' -d "$body" "$hook" \
      >/dev/null 2>&1 || true
  fi
  cat "$log" >&2
  exit 1
fi
exit 0
