#!/usr/bin/env bash
# Check the server is ready BEFORE deploying anything.
#
#   ./infra/preflight.sh
#
# Every check here corresponds to something that otherwise fails halfway
# through a deploy, at a point where the error message does not name the cause.
# The expensive one is the certificate: Let's Encrypt rate-limits failures, so
# discovering a bad DNS record during the real request costs you an hour.
#
# Exit 0 means go. Anything else, fix what it names and run it again.

set -uo pipefail   # deliberately not -e: every check should run

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"

G='\033[32m'; R='\033[31m'; Y='\033[33m'; D='\033[2m'; B='\033[1m'; O='\033[0m'
fails=0; warns=0

ok()   { printf "  ${G}ok${O}    %-34s ${D}%s${O}\n" "$1" "${2:-}"; }
bad()  { printf "  ${R}FAIL${O}  %-34s %s\n" "$1" "${2:-}"; fails=$((fails+1)); }
warn() { printf "  ${Y}warn${O}  %-34s ${D}%s${O}\n" "$1" "${2:-}"; warns=$((warns+1)); }

env_get() {
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"''
}

printf "${B}Preflight — is this server ready to deploy?${O}\n\n"

# ---------------------------------------------------------------- the machine
printf "${D}Machine${O}\n"

mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
if   [[ $mem_mb -ge 7500 ]]; then ok "memory" "${mem_mb} MB"
elif [[ $mem_mb -ge 5500 ]]; then warn "memory" "${mem_mb} MB — works, 8 GB is comfortable"
else bad "memory" "${mem_mb} MB — needs at least 6 GB, 8 GB recommended"; fi

disk_gb=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
if [[ ${disk_gb:-0} -ge 40 ]]; then ok "free disk" "${disk_gb} GB"
elif [[ ${disk_gb:-0} -ge 20 ]]; then warn "free disk" "${disk_gb} GB — images alone need ~6 GB"
else bad "free disk" "${disk_gb:-?} GB — not enough"; fi

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker" "$(docker --version | cut -d, -f1)"
  else
    bad "docker" "installed but not usable — log out and back in after usermod -aG docker"
  fi
else
  bad "docker" "not installed — curl -fsSL https://get.docker.com | sudo sh"
fi

docker compose version >/dev/null 2>&1 \
  && ok "docker compose" "$(docker compose version --short 2>/dev/null)" \
  || bad "docker compose" "v2 required — comes with the install script above"

if command -v python3 >/dev/null 2>&1; then
  pv=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
  python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)' \
    && ok "python3" "$pv" || bad "python3" "$pv — needs 3.9 or newer"
else
  bad "python3" "not installed — sudo apt install -y python3"
fi

# ------------------------------------------------------------------- the plan
printf "\n${D}Configuration${O}\n"

if [[ -f "$ENV_FILE" ]]; then
  ok "infra/.env exists"
else
  bad "infra/.env" "missing — cp infra/.env.example infra/.env"
fi

CRM_DOMAIN=$(env_get CRM_DOMAIN)
AUTO_DOMAIN=$(env_get AUTOMATION_DOMAIN)
ACME_EMAIL=$(env_get ACME_EMAIL)
SERVER_URL=$(env_get SERVER_URL)

for pair in "CRM_DOMAIN:$CRM_DOMAIN" "AUTOMATION_DOMAIN:$AUTO_DOMAIN" "ACME_EMAIL:$ACME_EMAIL"; do
  k=${pair%%:*}; v=${pair#*:}
  [[ -n "$v" ]] && ok "$k" "$v" || bad "$k" "not set in infra/.env"
done

for k in PG_DATABASE_PASSWORD ENCRYPTION_KEY APP_SECRET N8N_ENCRYPTION_KEY; do
  v=$(env_get "$k")
  if [[ -z "$v" ]]; then bad "$k" "not set — openssl rand -base64 32"
  elif [[ ${#v} -lt 24 ]]; then warn "$k" "looks short (${#v} chars)"
  else ok "$k" "set (${#v} chars)"; fi
done

# SERVER_URL mismatching the domain is the cause of the login redirect loop,
# and the symptom points at authentication rather than at this line.
if [[ -n "$SERVER_URL" && -n "$CRM_DOMAIN" ]]; then
  [[ "$SERVER_URL" == "https://$CRM_DOMAIN" ]] \
    && ok "SERVER_URL matches CRM_DOMAIN" "$SERVER_URL" \
    || bad "SERVER_URL" "is '$SERVER_URL', expected 'https://$CRM_DOMAIN' — a mismatch loops the login page forever"
fi

smtp_host=$(env_get EMAIL_SMTP_HOST)
[[ -n "$smtp_host" ]] \
  && ok "SMTP relay" "$smtp_host" \
  || warn "SMTP relay" "not set — the CRM cannot send email until EMAIL_SMTP_HOST is configured"

alert=$(env_get ALERT_WEBHOOK_URL)
case "$alert" in
  *alert-sink*) warn "ALERT_WEBHOOK_URL" "still the local dev sink — real alerts would be swallowed" ;;
  "")           warn "ALERT_WEBHOOK_URL" "not set — failures will be recorded but nobody notified" ;;
  *)            ok   "ALERT_WEBHOOK_URL" "configured" ;;
esac

# --------------------------------------------------------------- the network
printf "\n${D}Network${O}\n"

public_ip=$(curl -s --max-time 8 https://api.ipify.org 2>/dev/null || true)
if [[ -n "$public_ip" ]]; then
  ok "outbound internet" "seen as $public_ip"
  case "$public_ip" in
    10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*)
      warn "public address" "$public_ip is private — use docs/11, not the VPS path" ;;
  esac
else
  bad "outbound internet" "no connectivity — the image pull will fail"
fi

# DNS is the single most common reason a first deploy fails, and the failure
# surfaces as an opaque certificate error several minutes later.
for pair in "CRM:$CRM_DOMAIN" "AUTOMATION:$AUTO_DOMAIN"; do
  label=${pair%%:*}; name=${pair#*:}
  [[ -z "$name" ]] && continue
  resolved=$(getent hosts "$name" 2>/dev/null | awk '{print $1}' | head -1)
  if [[ -z "$resolved" ]]; then
    bad "DNS $label" "$name does not resolve — add the A record and wait for it"
  elif [[ -n "$public_ip" && "$resolved" != "$public_ip" ]]; then
    bad "DNS $label" "$name -> $resolved, but this server is $public_ip"
  else
    ok "DNS $label" "$name -> $resolved"
  fi
done

# Port 80 must be reachable from the internet or the HTTP-01 challenge fails.
# Anything already listening there will also block Caddy from starting.
if command -v ss >/dev/null 2>&1; then
  for p in 80 443; do
    if ss -lntH "sport = :$p" 2>/dev/null | grep -q .; then
      holder=$(ss -lntpH "sport = :$p" 2>/dev/null | grep -oP 'users:\(\("\K[^"]+' | head -1)
      warn "port $p" "already in use by ${holder:-something} — Caddy cannot bind"
    else
      ok "port $p" "free"
    fi
  done
fi

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  for p in 22 80 443; do
    ufw status 2>/dev/null | grep -qE "^$p(/tcp)?[[:space:]]+ALLOW" \
      && ok "firewall $p" "allowed" \
      || bad "firewall $p" "not allowed in ufw — sudo ufw allow $p/tcp"
  done
else
  warn "firewall" "ufw inactive — Contabo does not filter by default, so nothing is protecting this server"
fi

# ------------------------------------------------------------------- verdict
printf "\n%s\n" "$(printf '=%.0s' {1..64})"
if [[ $fails -gt 0 ]]; then
  printf "  ${R}%d blocker(s)${O}, %d warning(s). Fix the blockers, run this again.\n" "$fails" "$warns"
  printf "%s\n" "$(printf '=%.0s' {1..64})"
  exit 1
fi
printf "  ${G}Ready to deploy.${O}"
[[ $warns -gt 0 ]] && printf "  %d warning(s) above — read them first." "$warns"
printf "\n  Next: ./infra/deploy.sh\n"
printf "%s\n" "$(printf '=%.0s' {1..64})"
