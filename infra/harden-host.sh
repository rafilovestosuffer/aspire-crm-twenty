#!/usr/bin/env bash
# Host extras that Docker will not do for you. Run once on the VPS, as root,
# after ufw and Docker, before or after deploy.sh.
#
#   sudo ./infra/harden-host.sh
#
# Does not disable SSH password auth — Contabo's first login is often a
# password, and flipping that from a script is how people lock themselves out.
# Do that yourself after a key works: PasswordAuthentication no in sshd_config.

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y unattended-upgrades fail2ban curl

if [[ ! -f /etc/apt/apt.conf.d/20auto-upgrades ]]; then
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
fi

# Reboot on a security update is a surprise in the middle of a webinar.
if ! grep -q 'Automatic-Reboot' /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null; then
  echo 'Unattended-Upgrade::Automatic-Reboot "false";' \
    >> /etc/apt/apt.conf.d/50unattended-upgrades
fi

systemctl enable --now fail2ban unattended-upgrades 2>/dev/null \
  || systemctl enable --now fail2ban 2>/dev/null || true

echo
echo "  unattended-upgrades  enabled (no automatic reboot)"
echo "  fail2ban             enabled (ssh jail is the default)"
echo
echo "  Still on you:"
echo "    SSH keys, then PasswordAuthentication no"
echo "    Make https://github.com/rafilovestosuffer/aspire-crm-twenty private"
echo "    crontab: 0 * * * * $(cd "$(dirname "$0")" && pwd)/watchdog.sh"
echo "    off-box copies: $(cd "$(dirname "$0")" && pwd)/backup-offsite.sh user@host:/path/"
