#!/usr/bin/env bash
# Deploy to the server. One command, every step a gate.
#
#   ./infra/preflight.sh     # first — catches what would waste the evening
#   ./infra/deploy.sh
#
# Differs from rebuild.sh, which builds a demo machine:
#   - no demo data. This is the real system
#   - no --dev workflows. The alert sink would swallow real alerts, and the
#     failure probe throws on purpose
#   - the reverse proxy and real TLS are included
#   - the backup restore is verified here, on this machine's actual disk
#
# Safe to re-run. Every step is idempotent, so a failure part-way through is
# fixed by fixing the cause and running it again.

set -euo pipefail

export PYTHONUNBUFFERED=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

G='\033[32m'; R='\033[31m'; Y='\033[33m'; B='\033[1m'; D='\033[2m'; O='\033[0m'

OVERLAY=(-f "$HERE/docker-compose.yml" -f "$HERE/docker-compose.vps.yml")
if [[ "${1:-}" == "--internal" ]]; then
  # Private network: certificates come from Caddy's own CA, not Let's Encrypt.
  OVERLAY=(-f "$HERE/docker-compose.yml" -f "$HERE/docker-compose.internal.yml")
  shift
fi

TOTAL=10
step=0
run() {
  step=$((step + 1))
  printf "\n${B}[%d/%d] %s${O}\n" "$step" "$TOTAL" "$1"
  shift
  "$@"
}

# ---------------------------------------------------------------- preflight
if [[ -x "$HERE/preflight.sh" ]]; then
  printf "${B}Preflight${O}\n"
  if ! "$HERE/preflight.sh" >/tmp/preflight.$$ 2>&1; then
    cat /tmp/preflight.$$; rm -f /tmp/preflight.$$
    printf "\n${R}Preflight found blockers. Fix them and run this again.${O}\n"
    exit 1
  fi
  grep -E "warn" /tmp/preflight.$$ || true
  rm -f /tmp/preflight.$$
  printf "  ${G}ready${O}\n"
fi

# ------------------------------------------------------------------- deploy
start_stack() {
  docker compose "${OVERLAY[@]}" pull --quiet 2>/dev/null \
    || docker compose "${OVERLAY[@]}" pull
  # `worker` waits on the server being healthy; on a first boot the migrations
  # can outlast that grace period and compose exits non-zero even though the
  # server is fine. The health wait below is the real gate.
  docker compose "${OVERLAY[@]}" up -d \
    || printf "  ${Y}compose reported an error — checking what is actually up${O}\n"

  printf "  waiting for the CRM (first boot runs migrations, up to 5 minutes)"
  for i in $(seq 1 60); do
    if docker compose "${OVERLAY[@]}" exec -T server \
         curl -fsS http://localhost:3000/healthz >/dev/null 2>&1; then
      printf "\n  ${G}ok${O}    CRM healthy\n"; return 0
    fi
    printf "."; sleep 5
  done
  printf "\n  ${R}FAIL${O}  never became healthy — docker compose %s logs server\n" "${OVERLAY[*]}"
  return 1
}

check_worker() {
  # The interface is perfectly healthy while this container is dead, and a dead
  # worker means no scheduled automation and no mailbox sync — silently.
  local id state
  id=$(docker compose "${OVERLAY[@]}" ps -aq worker 2>/dev/null | head -1)
  state=$(docker inspect --format '{{.State.Status}}' "$id" 2>/dev/null || echo missing)
  if [[ "$state" != "running" ]]; then
    printf "  worker is '%s' — starting it\n" "$state"
    docker compose "${OVERLAY[@]}" up -d worker >/dev/null 2>&1 || true
    sleep 5
    state=$(docker inspect --format '{{.State.Status}}' \
             "$(docker compose "${OVERLAY[@]}" ps -aq worker | head -1)" 2>/dev/null || echo missing)
  fi
  [[ "$state" == "running" ]] || { printf "  ${R}worker not running${O}\n"; return 1; }
  printf "  ${G}ok${O}    worker running\n"
}

run "Start the stack"             start_stack
run "Check the worker"            check_worker
run "Create the CRM workspace"    python3 scripts/bootstrap_workspace.py
run "Create the automation owner" python3 scripts/bootstrap_n8n.py
run "Build the object model"      python3 scripts/twenty_provision.py
run "Create credentials"          python3 scripts/n8n_credentials.py
run "Validate every CRM API call" python3 scripts/validate_workflow_queries.py
run "Deploy and activate"         python3 scripts/n8n_deploy.py --activate
run "Verify every layer"          python3 scripts/stack_verify.py
run "Prove the backups restore"   python3 scripts/verify_restore.py

CRM=$(grep -E '^CRM_DOMAIN=' "$HERE/.env" | cut -d= -f2- | tr -d '"')
AUTO=$(grep -E '^AUTOMATION_DOMAIN=' "$HERE/.env" | cut -d= -f2- | tr -d '"')

cat <<EOF

$(printf '=%.0s' {1..64})
  ${G}Deployed.${O}

  CRM          https://${CRM}
  Automation   https://${AUTO}
  Public form  https://${AUTO}/form/aspire-contact

  ${Y}Do these three things now, not tomorrow:${O}

  1. Change the admin password in the CRM (Settings → Members), and the
     automation owner password too. Both defaults are in a public repository.

  2. Confirm the certificate is real — open the CRM and check the padlock.
     A warning means Let's Encrypt could not reach this server on port 80.

  3. Restrict the automation editor. infra/Caddyfile ships with placeholder
     IP ranges that match nothing, so the editor is currently refusing
     everyone. Put your office and VPN ranges in, then:
       docker compose ${OVERLAY[*]} up -d --force-recreate caddy

  There is no data in the CRM yet — that is Phase 4, and the GoHighLevel
  suppression list must be exported before any termination date is agreed.
$(printf '=%.0s' {1..64})
EOF
