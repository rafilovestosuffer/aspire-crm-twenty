#!/usr/bin/env bash
# Copy the on-disk dumps off this machine. Same-disk backups survive a bad
# migration, not a dead VPS.
#
#   ./infra/backup-offsite.sh user@backup-host:/srv/aspire-crm/
#   BACKUP_OFFSITE=user@backup-host:/srv/aspire-crm/ ./infra/backup-offsite.sh
#
# Destination is rsync's. The dumps include Postgres (custom format) and the
# Twenty / n8n file tarballs. This does not copy infra/.env — put ENCRYPTION_KEY
# and N8N_ENCRYPTION_KEY in a password manager separately.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/backups/"
DEST="${1:-${BACKUP_OFFSITE:-}}"

if [[ -z "$DEST" ]]; then
  echo "usage: $0 user@backup-host:/path/" >&2
  echo "   or: BACKUP_OFFSITE=user@host:/path/ $0" >&2
  exit 2
fi

if [[ ! -d "$SRC" ]]; then
  echo "no $SRC yet — the first dump runs 24h after the stack starts, or run:" >&2
  echo "  python3 scripts/verify_restore.py" >&2
  exit 1
fi

mkdir -p "$SRC"
rsync -az --delete "$SRC" "$DEST"
echo "copied $SRC -> $DEST"
