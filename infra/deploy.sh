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
MODE_ARGS=()
INTERNAL=0
if [[ "${1:-}" == "--internal" ]]; then
  INTERNAL=1
  # Private network: certificates come from Caddy's own CA, not Let's Encrypt.
  OVERLAY=(-f "$HERE/docker-compose.yml" -f "$HERE/docker-compose.internal.yml")
  # Preflight has to know too, or it fails the deploy on a missing public IP
  # and a public DNS record that an internal server is not supposed to have.
  MODE_ARGS=(--internal)
  shift
fi

TOTAL=11
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
  # The ${a[@]+...} form is not decoration: under `set -u`, expanding an empty
  # array is an unbound-variable error on bash before 4.4, which ships on
  # machines this may well land on.
  if ! "$HERE/preflight.sh" ${MODE_ARGS[@]+"${MODE_ARGS[@]}"} \
        >/tmp/preflight.$$ 2>&1; then
    cat /tmp/preflight.$$; rm -f /tmp/preflight.$$
    printf "\n${R}Preflight found blockers. Fix them and run this again.${O}\n"
    exit 1
  fi
  grep -E "warn" /tmp/preflight.$$ || true
  rm -f /tmp/preflight.$$
  printf "  ${G}ready${O}\n"
fi

# ------------------------------------------------------------------- deploy

# Twenty's REST metadata API is not served in-process. The server makes an HTTP
# call to ${SERVER_URL}${path} and returns what comes back, so it has to be able
# to reach — and trust — its own public URL. Internally the certificate comes
# from Caddy's own CA, which Node does not trust, and the failure is silent in
# the worst way: every /rest/metadata/* request answers 500 with an empty body
# and writes no log line at all, taking the provisioner, the query validator and
# the stack check down together without naming a cause.
#
# Caddy keeps its PKI at 0700 owned by root and these containers run as a
# non-root user, so the root is copied out rather than mounted in place.
trust_internal_ca() {
  local dest="$HERE/certs/caddy-root.crt" tmp
  mkdir -p "$HERE/certs"
  tmp=$(mktemp)
  if ! docker compose "${OVERLAY[@]}" exec -T caddy \
        cat /data/caddy/pki/authorities/local/root.crt >"$tmp" 2>/dev/null \
     || [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    printf "  ${Y}warn${O}  could not read Caddy's CA root — the metadata API will fail\n"
    return 0
  fi
  if cmp -s "$tmp" "$dest" 2>/dev/null; then
    rm -f "$tmp"
    printf "  ${G}ok${O}    internal CA already trusted\n"
    return 0
  fi
  # World-readable on purpose: it is a public certificate, and the containers
  # that must read it do not run as this user.
  install -m 0644 "$tmp" "$dest"; rm -f "$tmp"
  printf "  ${G}ok${O}    internal CA exported to infra/certs — restarting the CRM\n"
  docker compose "${OVERLAY[@]}" up -d --force-recreate server worker >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do
    docker compose "${OVERLAY[@]}" exec -T server \
      curl -fsS http://localhost:3000/healthz >/dev/null 2>&1 && return 0
    sleep 5
  done
  printf "  ${R}FAIL${O}  the CRM did not come back after the CA restart\n"
  return 1
}

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
      printf "\n  ${G}ok${O}    CRM healthy\n"
      [[ $INTERNAL -eq 1 ]] && { trust_internal_ca || return 1; }
      return 0
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
run "Prove the proxy rules hold"  python3 scripts/verify_proxy_rules.py ${MODE_ARGS[@]+"${MODE_ARGS[@]}"}

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
