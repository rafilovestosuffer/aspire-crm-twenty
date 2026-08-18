#!/usr/bin/env bash
# Prepare infra/.env for the public company server.
#
#   ./infra/init-vps-env.sh --yes
#
# Laptop .env.example is full of localhost. Copying it onto the VPS and only
# filling the "VPS deployment" block at the bottom leaves N8N_HOST=localhost
# and ALERT_WEBHOOK_URL pointing at a --dev workflow that is not deployed
# here. Forms then render and submit nowhere, and every alert 404s inside the
# error handler. This script sets the public hostnames, generates the four
# secrets, and lists the lines only a person can fill (SMTP, office IPs, chat
# webhook, payment links).
#
# Run on the VPS, once. Re-running is safe: existing secrets are kept.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
EXAMPLE="$HERE/.env.example"

G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; D=$'\033[2m'; O=$'\033[0m'

if [[ "${1:-}" != "--yes" ]]; then
  cat <<EOF
${B}Rewrite infra/.env for crm.aspiretss.com / auto.aspiretss.com.${O}

This is for the company VPS, not a laptop. It will:

  - copy .env.example if infra/.env does not exist
  - generate PG_DATABASE_PASSWORD, ENCRYPTION_KEY, APP_SECRET,
    N8N_ENCRYPTION_KEY if they are blank (never overwrite a set secret)
  - set the public hostnames and https
  - clear LIVE_MAIL_ALLOWLIST and the local alert-sink URL
  - leave SMTP user/password, office IPs, the chat webhook and the
    six payment links for you to paste

Run:  ./infra/init-vps-env.sh --yes
EOF
  exit 2
fi

if [[ ! -f "$EXAMPLE" ]]; then
  echo "missing $EXAMPLE" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE" "$ENV_FILE"
  printf "  ${G}ok${O}    created infra/.env from the example\n"
else
  printf "  ${D}..${O}    infra/.env already exists — keeping set secrets\n"
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required — sudo apt install -y openssl" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required — sudo apt install -y python3" >&2
  exit 1
fi

set_env() {
  # $1=key $2=value  — always write
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding="utf-8").read().splitlines()
found = False
out = []
for line in lines:
    raw = line.strip()
    if raw and not raw.startswith("#") and raw.split("=", 1)[0].strip() == key:
        out.append(f"{key}={val}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"{key}={val}")
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
}

env_get() {
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
for line in open(path, encoding="utf-8"):
    raw = line.strip()
    if raw and not raw.startswith("#") and "=" in raw:
        k, _, v = raw.partition("=")
        if k.strip() == key:
            print(v.strip().strip("\"'"))
PY
}

set_env_if_blank() {
  local cur
  cur="$(env_get "$1")"
  if [[ -z "$cur" ]]; then
    set_env "$1" "$2"
    printf "  ${G}ok${O}    set %s\n" "$1"
  else
    printf "  ${D}ok${O}    %s already set\n" "$1"
  fi
}

gen_secret() {
  openssl rand -base64 32 | tr -d '/+=' | head -c 32
  echo
}

for k in PG_DATABASE_PASSWORD ENCRYPTION_KEY APP_SECRET N8N_ENCRYPTION_KEY; do
  if [[ -z "$(env_get "$k")" ]]; then
    set_env "$k" "$(gen_secret)"
    printf "  ${G}ok${O}    generated %s\n" "$k"
  else
    printf "  ${D}ok${O}    %s already set — not rotated\n" "$k"
  fi
done

# Public names. Overwrite localhost leftovers from .env.example.
# N8N_PROTOCOL is always https here — this script is VPS-only.
set_env N8N_PROTOCOL "https"
printf "  ${G}ok${O}    N8N_PROTOCOL=https\n"

for pair in \
  "SERVER_URL=https://crm.aspiretss.com" \
  "N8N_HOST=auto.aspiretss.com" \
  "N8N_PUBLIC_URL=https://auto.aspiretss.com" \
  "CRM_DOMAIN=crm.aspiretss.com" \
  "AUTOMATION_DOMAIN=auto.aspiretss.com"
do
  k=${pair%%=*}; want=${pair#*=}
  cur="$(env_get "$k")"
  case "$cur" in
    ""|http://localhost*|https://localhost*|localhost|http)
      set_env "$k" "$want"
      printf "  ${G}ok${O}    %s=%s\n" "$k" "$want"
      ;;
    "$want")
      printf "  ${D}ok${O}    %s already %s\n" "$k" "$want"
      ;;
    *)
      printf "  ${Y}keep${O}  %s=%s (not the default hostname)\n" "$k" "$cur"
      ;;
  esac
done

set_env_if_blank ACME_EMAIL "it@aspiretss.com"
set_env_if_blank EMAIL_DRIVER "smtp"
set_env_if_blank EMAIL_SMTP_HOST "smtp-relay.gmail.com"
set_env_if_blank EMAIL_SMTP_PORT "587"
set_env_if_blank EMAIL_SMTP_SECURE "false"
set_env_if_blank EMAIL_FROM_NAME "Aspire Tech"
set_env_if_blank EMAIL_FROM_ADDRESS "it@aspiretss.com"
set_env_if_blank ASPIRE_FROM_EMAIL "it@aspiretss.com"
set_env_if_blank TZ "UTC"

# A copied laptop demo must not gate production mail or swallow alerts.
set_env LIVE_MAIL_ALLOWLIST ""
printf "  ${G}ok${O}    LIVE_MAIL_ALLOWLIST emptied\n"

alert="$(env_get ALERT_WEBHOOK_URL)"
case "$alert" in
  *alert-sink*|http://localhost*|http://n8n:5678*)
    set_env ALERT_WEBHOOK_URL ""
    printf "  ${G}ok${O}    ALERT_WEBHOOK_URL cleared (was the local sink)\n"
    ;;
esac

chmod 600 "$ENV_FILE"

need=()
[[ -z "$(env_get N8N_EDITOR_ALLOWED_IPS)" ]] && need+=("N8N_EDITOR_ALLOWED_IPS   office/VPN CIDRs — from the office: curl ifconfig.me")
[[ -z "$(env_get EMAIL_SMTP_USER)" ]] && need+=("EMAIL_SMTP_USER          Workspace user")
[[ -z "$(env_get EMAIL_SMTP_PASSWORD)" ]] && need+=("EMAIL_SMTP_PASSWORD      app password, not the account password")
[[ -z "$(env_get ALERT_WEBHOOK_URL)" ]] && need+=("ALERT_WEBHOOK_URL        Google Chat / Slack incoming webhook")
[[ -z "$(env_get ENROL_PAYMENT_LINKS)" ]] && need+=("ENROL_PAYMENT_LINKS      six FastPayDirect URLs from GHL Trigger Links")

cat <<EOF

  ${B}Back up ENCRYPTION_KEY and N8N_ENCRYPTION_KEY off this machine.${O}
  Lose the first and every secret Twenty holds becomes unreadable.

EOF

if ((${#need[@]})); then
  printf "  ${Y}Still to paste into infra/.env:${O}\n\n"
  for line in "${need[@]}"; do
    printf "    %s\n" "$line"
  done
  cat <<EOF

  Then:
    nano infra/.env
    ./infra/preflight.sh
    ./infra/deploy.sh
EOF
else
  cat <<EOF
  ${G}Every required line is set.${O}

    ./infra/preflight.sh
    ./infra/deploy.sh
EOF
fi
