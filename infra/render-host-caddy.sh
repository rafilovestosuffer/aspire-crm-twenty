#!/usr/bin/env bash
# Write the site file for a host that already runs its own Caddy.
#
#   ./infra/render-host-caddy.sh
#   sudo cp infra/aspire-crm.caddy /etc/caddy/sites/aspire-crm.caddy
#   sudo caddy validate --config /etc/caddy/Caddyfile
#   sudo systemctl reload caddy
#
# Same rules as infra/Caddyfile (public form, webhooks and OAuth callback;
# editor refused outside N8N_EDITOR_ALLOWED_IPS), with the values filled in,
# because the system Caddy does not read infra/.env. Upstreams are the
# loopback ports from docker-compose.hostproxy.yml.
#
# Re-run and re-copy after changing the domains, ports or the allowlist.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
OUT="$HERE/aspire-crm.caddy"

env_get() {
  grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"''
}

[[ -f "$ENV_FILE" ]] || { echo "missing infra/.env — run ./infra/init-vps-env.sh --yes first" >&2; exit 1; }

CRM=$(env_get CRM_DOMAIN)
AUTO=$(env_get AUTOMATION_DOMAIN)
ALLOW=$(env_get N8N_EDITOR_ALLOWED_IPS)
TPORT=$(env_get TWENTY_PORT); TPORT=${TPORT:-3000}
NPORT=$(env_get N8N_PORT);    NPORT=${NPORT:-5678}

missing=()
[[ -n "$CRM" ]]   || missing+=(CRM_DOMAIN)
[[ -n "$AUTO" ]]  || missing+=(AUTOMATION_DOMAIN)
# Empty would make `not remote_ip` match nothing and leave the editor open.
[[ -n "$ALLOW" ]] || missing+=(N8N_EDITOR_ALLOWED_IPS)
if ((${#missing[@]})); then
  echo "not set in infra/.env: ${missing[*]}" >&2
  exit 1
fi

cat >"$OUT" <<EOF
# Aspire CRM — written by infra/render-host-caddy.sh from infra/.env.
# Edit infra/.env and re-run the script rather than editing this file.

(aspire_crm_hardening) {
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "SAMEORIGIN"
		Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}
}

${CRM} {
	import aspire_crm_hardening
	reverse_proxy 127.0.0.1:${TPORT} {
		flush_interval -1
	}
}

${AUTO} {
	import aspire_crm_hardening

	@public path /webhook/* /webhook-test/* /form/* /rest/oauth2-credential/*
	handle @public {
		reverse_proxy 127.0.0.1:${NPORT}
	}

	handle {
		@untrusted not remote_ip ${ALLOW}
		respond @untrusted "Not available from this network" 403
		reverse_proxy 127.0.0.1:${NPORT}
	}
}
EOF

printf '  ok    wrote infra/aspire-crm.caddy  (%s -> :%s, %s -> :%s)\n' "$CRM" "$TPORT" "$AUTO" "$NPORT"
cat <<EOF

  Install it into the host Caddy:
    sudo cp infra/aspire-crm.caddy /etc/caddy/sites/aspire-crm.caddy
    sudo caddy validate --config /etc/caddy/Caddyfile
    sudo systemctl reload caddy

  reload, not restart: the other sites on this host stay up, and a bad file
  is rejected while the old config keeps serving.
EOF
