#!/usr/bin/env bash
# Zero to proven, in one command.
#
#   ./infra/rebuild.sh              build on top of whatever is there
#   ./infra/rebuild.sh --destroy    wipe the databases first, then build
#
# Every step is a gate: the script stops at the first failure rather than
# carrying on and reporting success at the end. That matters here because the
# expensive defects in this build were all silent — a workflow deployed but
# never activated, a query that returned the whole table instead of one row.
# A green run of this script is the claim that the stack works; it should fail
# loudly rather than overstate.
#
# Takes roughly 12 minutes from a clean slate, most of it Twenty's first-boot
# migrations and the demo seed.

set -euo pipefail

# Python block-buffers stdout when it is a file rather than a terminal, so a
# redirected run shows nothing for minutes at a time and looks hung during the
# seed. Progress on a twelve-minute script is worth the unbuffered writes.
export PYTHONUNBUFFERED=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

env_get() {
  local f="$HERE/.env" line
  [[ -f "$f" ]] || return 0
  line="$(grep -E "^$1=" "$f" 2>/dev/null | tail -1 || true)"
  printf '%s' "${line#*=}" | tr -d '"'"'"''
}

# Catcher hosts only. Anything else is a real relay — the current proof suite
# submits proof.<tag>@northgate-<tag>.com, and those must never leave the host.
smtp_is_live() {
  local host
  host="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$host" in
    ""|mailpit|localhost|127.0.0.1) return 1 ;;
    *) return 0 ;;
  esac
}

prove_workflows() {
  local host live
  host="$(env_get EMAIL_SMTP_HOST)"
  live="$(env_get LIVE_DEMO_EMAIL)"
  if smtp_is_live "$host"; then
    if [[ -z "$live" ]]; then
      echo "ERROR: EMAIL_SMTP_HOST=${host} is a real relay but LIVE_DEMO_EMAIL is empty." >&2
      echo "       This suite submits the form as proof.<tag>@northgate-<tag>.com" >&2
      echo "       — those are not real, and Gmail would still try to deliver." >&2
      echo "       Set LIVE_DEMO_EMAIL and LIVE_MAIL_ALLOWLIST to your address in" >&2
      echo "       infra/.env (and recreate n8n so the allowlist reaches the container)," >&2
      echo "       or clear EMAIL_SMTP_* to use Mailpit." >&2
      exit 1
    fi
    python3 scripts/prove_workflows.py --live-email "$live"
  else
    python3 scripts/prove_workflows.py
  fi
}

step=0
run() {
  step=$((step + 1))
  printf '\n\033[1m[%d/%d] %s\033[0m\n' "$step" "$TOTAL" "$1"
  shift
  "$@"
}
TOTAL=11

if [[ "${1:-}" == "--destroy" ]]; then
  echo "DESTROY" | "$HERE/up.sh" --destroy
  # The API keys in infra/.env belong to the database that was just deleted.
  # Left in place they are silently wrong: scripts authenticate against a
  # workspace that no longer exists and fail in confusing ways much later.
  python3 - <<'PY'
import pathlib
p = pathlib.Path("infra/.env")
if p.exists():
    out = []
    for line in p.read_text().splitlines():
        k = line.split("=", 1)[0].strip()
        out.append(f"{k}=" if k in ("TWENTY_API_KEY", "N8N_API_KEY") else line)
    p.write_text("\n".join(out) + "\n")
    print("  cleared stale API keys from infra/.env")
PY
fi

run "Start the stack"            "$HERE/up.sh"
run "Verify every layer"         python3 scripts/stack_verify.py
run "Create Twenty workspace"    python3 scripts/bootstrap_workspace.py
run "Create n8n owner + API key" python3 scripts/bootstrap_n8n.py
run "Provision the object model" python3 scripts/twenty_provision.py
# --wipe makes the step idempotent: without it a second run of this
# script doubles every demo record instead of reproducing the dataset.
run "Seed demo data"             python3 scripts/seed_demo_data.py --wipe
run "Create n8n credentials"     python3 scripts/n8n_credentials.py
run "Validate every Twenty call" python3 scripts/validate_workflow_queries.py
run "Deploy and activate"        python3 scripts/n8n_deploy.py --dev --activate
run "Prove the workflows"        prove_workflows
# Last, not first: the worker can be dead while every other check passes,
# and a build that ends green with no worker has no scheduled jobs and no
# mailbox sync. Re-checking here catches it after everything has settled.
run "Re-verify every layer"      python3 scripts/stack_verify.py

printf '\n\033[32m  ok  \033[0m stack rebuilt and proven\n'
printf '        Twenty   http://localhost:3000\n'
printf '        n8n      http://localhost:5678\n'
printf '        Mailpit  http://localhost:8025\n'
