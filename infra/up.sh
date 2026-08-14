#!/usr/bin/env bash
# Bring up the Aspire CRM stack. Safe to re-run.
#
#   ./infra/up.sh              start (generates any missing secrets first)
#   ./infra/up.sh --vps        start with the VPS overlay (loopback binding)
#   ./infra/up.sh --down       stop, keep data
#   ./infra/up.sh --destroy    stop and DELETE ALL DATA
#
# Generated secrets are written to infra/.env, which is gitignored.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
COMPOSE=(docker compose -f "$HERE/docker-compose.yml")

c_ok()   { printf '\033[32m  ok  \033[0m %s\n' "$1"; }
c_warn() { printf '\033[33m  !!  \033[0m %s\n' "$1"; }
c_info() { printf '\033[36m  ..  \033[0m %s\n' "$1"; }

for arg in "$@"; do
  case "$arg" in
    --vps) COMPOSE+=(-f "$HERE/docker-compose.vps.yml") ;;
    --down) "${COMPOSE[@]}" down; c_ok "stopped — data kept"; exit 0 ;;
    --destroy)
      read -rp "This deletes the database and every workflow. Type DESTROY: " a
      [[ "$a" == "DESTROY" ]] || { echo "aborted"; exit 1; }
      "${COMPOSE[@]}" down -v; c_ok "destroyed"; exit 0 ;;
  esac
done

command -v docker >/dev/null || { echo "docker not found"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 required"; exit 1; }

# ---------------------------------------------------------------- .env setup
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$HERE/.env.example" "$ENV_FILE"
  c_info "created infra/.env from the example"
fi

# Fill any blank secret. Never overwrites one that already has a value —
# regenerating ENCRYPTION_KEY against an existing database makes every stored
# secret unreadable.
gen_if_blank() {
  local key="$1" val
  val="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2-)"
  if [[ -z "$val" ]]; then
    # No special characters: these values land inside connection URLs.
    val="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
    # BSD and GNU sed disagree on -i, so rewrite the file instead.
    awk -v k="$key" -v v="$val" \
      'BEGIN{FS=OFS="="} $1==k && NF>=1 {print k "=" v; next} {print}' \
      "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    c_ok "generated $key"
  fi
}

gen_if_blank PG_DATABASE_PASSWORD
gen_if_blank ENCRYPTION_KEY
gen_if_blank APP_SECRET
gen_if_blank N8N_ENCRYPTION_KEY

chmod 600 "$ENV_FILE"

cat <<'WARN'

  ─────────────────────────────────────────────────────────────
   Back up ENCRYPTION_KEY and N8N_ENCRYPTION_KEY from
   infra/.env to somewhere other than this machine.

   Lose ENCRYPTION_KEY and every secret Twenty holds — OAuth
   tokens, connected mailboxes, application variables — becomes
   permanently unreadable. There is no recovery.
  ─────────────────────────────────────────────────────────────

WARN

# ------------------------------------------------------------------- startup
mkdir -p "$HERE/backups"

c_info "pulling images (first run takes a few minutes)"
"${COMPOSE[@]}" pull --quiet 2>/dev/null || "${COMPOSE[@]}" pull

c_info "starting"
"${COMPOSE[@]}" up -d

# Twenty runs migrations on first boot; that is the slow part.
c_info "waiting for Twenty to become healthy (up to 5 minutes on first run)"
for i in $(seq 1 60); do
  state="$("${COMPOSE[@]}" ps --format json server 2>/dev/null \
           | python3 -c 'import sys,json
raw=sys.stdin.read().strip()
try:
    d=json.loads(raw if raw.startswith("[") else "["+",".join(raw.splitlines())+"]")
    print(d[0].get("Health") or d[0].get("State") or "?")
except Exception: print("?")' 2>/dev/null || echo "?")"
  [[ "$state" == "healthy" ]] && { c_ok "Twenty healthy"; break; }
  [[ $i -eq 60 ]] && c_warn "still not healthy — check: docker compose -f infra/docker-compose.yml logs server"
  sleep 5
done

# The UI can look perfectly fine while this container is dead, which is why it
# is checked separately rather than inferred from the server being up.
# `ps -q` also lists containers merely Created, which is how a worker that
# never started still looked fine. Check the actual running state.
worker_state="$(docker inspect --format '{{.State.Status}}' \
                 "$("${COMPOSE[@]}" ps -aq worker 2>/dev/null | head -1)" 2>/dev/null || echo "missing")"
if [[ "$worker_state" == "running" ]]; then
  c_ok "worker running"
else
  c_warn "worker is '$worker_state', not running — starting it"
  "${COMPOSE[@]}" up -d worker >/dev/null 2>&1 || true
  sleep 5
  worker_state="$(docker inspect --format '{{.State.Status}}' \
                   "$("${COMPOSE[@]}" ps -aq worker 2>/dev/null | head -1)" 2>/dev/null || echo "missing")"
  [[ "$worker_state" == "running" ]] \
    && c_ok "worker running" \
    || c_warn "worker STILL not running — scheduled workflows and mailbox sync will not fire"
fi

SERVER_URL="$(grep -E '^SERVER_URL=' "$ENV_FILE" | cut -d= -f2-)"
N8N_URL="$(grep -E '^N8N_PUBLIC_URL=' "$ENV_FILE" | cut -d= -f2-)"

cat <<EOF

  Twenty  ${SERVER_URL:-http://localhost:3000}
  n8n     ${N8N_URL:-http://localhost:5678}

  Next:
    1. Open Twenty, create the workspace and your admin user
    2. Settings → API & Webhooks → create a key → put it in infra/.env
       as TWENTY_API_KEY
    3. Open n8n, Settings → n8n API → create a key → N8N_API_KEY
    4. python scripts/stack_verify.py

EOF
