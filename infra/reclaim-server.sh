#!/usr/bin/env bash
# Take a server that is already running something else — Qdrant, in our case —
# and hand it to this stack, without throwing away data that nobody agreed to
# throw away.
#
#   ./infra/reclaim-server.sh              inventory only. Changes nothing
#   ./infra/reclaim-server.sh --yes        archive Qdrant's data, then stop it
#   ./infra/reclaim-server.sh --yes --purge   also delete its volumes and images
#
# The default is a report because "kill qdrant" and "delete the vector database"
# are the same command from the shell's point of view and very different
# sentences to have to say afterwards. Stopping is reversible; --purge is not,
# and it asks you to type the word.
#
# What it looks for, in this order, because a thing can be running in any of
# these ways and only one of them shows up in `docker ps`:
#   1. containers whose name or image mentions qdrant
#   2. a compose project directory (~/qdrant and friends)
#   3. a systemd unit
#   4. anything listening on 6333/6334, whatever it calls itself
#
# It also reports what holds ports 80 and 443, since Caddy cannot bind them and
# preflight only warns. It does not kill those for you: on a server you did not
# build, the thing on port 80 is as likely to be the reason someone still has
# access as it is to be junk.

set -uo pipefail   # not -e: every check should run even after one fails

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

G='\033[32m'; R='\033[31m'; Y='\033[33m'; D='\033[2m'; B='\033[1m'; O='\033[0m'

APPLY=0
PURGE=0
ARCHIVE_DIR="${RECLAIM_ARCHIVE_DIR:-$HOME/qdrant-archive}"

for arg in "$@"; do
  case "$arg" in
    --yes|-y)  APPLY=1 ;;
    --purge)   PURGE=1 ;;
    -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ok()   { printf "  ${G}ok${O}    %-30s ${D}%s${O}\n" "$1" "${2:-}"; }
act()  { printf "  ${B}->${O}    %-30s %s\n" "$1" "${2:-}"; }
bad()  { printf "  ${R}FAIL${O}  %-30s %s\n" "$1" "${2:-}"; }
warn() { printf "  ${Y}warn${O}  %-30s ${D}%s${O}\n" "$1" "${2:-}"; }
note() { printf "        ${D}%s${O}\n" "$1"; }

need_docker() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

# sudo only where it is actually needed (systemd, ss -p for other users'
# processes). Silent no-op when we are already root or sudo is absent.
SUDO=""
if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

printf "${B}Reclaim this server for the Aspire CRM stack${O}\n"
[[ $APPLY -eq 0 ]] && printf "${D}Inventory only — nothing will be changed. Add --yes to act.${O}\n"
printf "\n"

found_any=0

# ------------------------------------------------------------------ containers
printf "${D}Docker${O}\n"
QDRANT_CONTAINERS=()
if need_docker; then
  ok "docker" "$(docker --version | cut -d, -f1)"
  while read -r id image names; do
    [[ -z "$id" ]] && continue
    QDRANT_CONTAINERS+=("$id")
    state=$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null)
    restart=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$id" 2>/dev/null)
    act "container $names" "$image ($state, restart=$restart)"
  done < <(docker ps -a --format '{{.ID}} {{.Image}} {{.Names}}' 2>/dev/null \
             | grep -i qdrant || true)

  if [[ ${#QDRANT_CONTAINERS[@]} -eq 0 ]]; then
    ok "qdrant containers" "none"
  else
    found_any=1
  fi
else
  bad "docker" "not usable here — run this on the server, as a user in the docker group"
  exit 1
fi

# --------------------------------------------------------------------- storage
# Where the data actually is decides whether an archive is a tar of a directory
# or a tar out of a named volume. Both end up in the same place.
printf "\n${D}Data${O}\n"
BIND_SOURCES=()
NAMED_VOLUMES=()
for id in ${QDRANT_CONTAINERS[@]+"${QDRANT_CONTAINERS[@]}"}; do
  while IFS='|' read -r type name source dest; do
    [[ -z "${type:-}" ]] && continue
    case "$type" in
      bind)   BIND_SOURCES+=("$source");  act "bind mount" "$source -> $dest" ;;
      volume) NAMED_VOLUMES+=("$name");   act "volume" "$name -> $dest" ;;
    esac
  done < <(docker inspect -f '{{range .Mounts}}{{.Type}}|{{.Name}}|{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' "$id" 2>/dev/null)
done

# A compose project left on disk restarts the whole thing on the next reboot
# even after the containers are gone, so it is part of the inventory.
COMPOSE_DIRS=()
for d in "$HOME/qdrant" "$HOME/qdrant-docker" /opt/qdrant /srv/qdrant; do
  if [[ -d "$d" ]]; then
    COMPOSE_DIRS+=("$d")
    size=$(du -sh "$d" 2>/dev/null | cut -f1)
    compose=""
    for f in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
      [[ -f "$d/$f" ]] && compose="$f"
    done
    act "directory" "$d (${size:-?}${compose:+, $compose})"
    found_any=1
  fi
done

[[ ${#BIND_SOURCES[@]} -eq 0 && ${#NAMED_VOLUMES[@]} -eq 0 && ${#COMPOSE_DIRS[@]} -eq 0 ]] \
  && ok "qdrant data" "nothing found"

# --------------------------------------------------------------------- systemd
printf "\n${D}Service manager${O}\n"
SYSTEMD_UNITS=()
if command -v systemctl >/dev/null 2>&1; then
  while read -r unit _; do
    [[ -z "$unit" ]] && continue
    SYSTEMD_UNITS+=("$unit")
    act "systemd unit" "$unit ($(systemctl is-enabled "$unit" 2>/dev/null || echo '?'))"
    found_any=1
  done < <(systemctl list-units --type=service --all --no-legend --plain 2>/dev/null \
             | awk '{print $1}' | grep -i qdrant || true)
  [[ ${#SYSTEMD_UNITS[@]} -eq 0 ]] && ok "systemd units" "none"
else
  ok "systemd" "not present"
fi

# ----------------------------------------------------------------------- ports
printf "\n${D}Ports${O}\n"
port_holder() {
  $SUDO ss -lntpH "sport = :$1" 2>/dev/null \
    | grep -oP 'users:\(\("\K[^"]+' | sort -u | paste -sd, -
}
if command -v ss >/dev/null 2>&1; then
  for p in 6333 6334; do
    h=$(port_holder "$p")
    [[ -n "$h" ]] && { act "port $p" "held by $h (qdrant's own)"; found_any=1; } \
                  || ok "port $p" "free"
  done
  # These two are the ones that matter to us. Reported, never killed.
  for p in 80 443; do
    h=$(port_holder "$p")
    if [[ -n "$h" ]]; then
      warn "port $p" "held by $h — Caddy cannot bind it. Stop that yourself; this script will not"
    else
      ok "port $p" "free — Caddy can bind"
    fi
  done
  for p in 3000 5678; do
    h=$(port_holder "$p")
    [[ -n "$h" ]] && warn "port $p" "held by $h — Twenty/n8n want this" || ok "port $p" "free"
  done
else
  warn "ss" "not installed — cannot check ports (sudo apt install -y iproute2)"
fi

# --------------------------------------------------------------------- verdict
printf "\n%s\n" "$(printf '=%.0s' {1..64})"

if [[ $found_any -eq 0 ]]; then
  printf "  ${G}Nothing of Qdrant's found.${O} The server is already clear.\n"
  printf "  Next: ./infra/preflight.sh\n"
  printf "%s\n" "$(printf '=%.0s' {1..64})"
  exit 0
fi

if [[ $APPLY -eq 0 ]]; then
  cat <<EOF
  Found the above. Nothing was changed.

  To archive the data and stop it:      ./infra/reclaim-server.sh --yes
  To also delete volumes and images:    ./infra/reclaim-server.sh --yes --purge

  The archive is written to $ARCHIVE_DIR and is not deleted by --purge.
EOF
  printf "%s\n" "$(printf '=%.0s' {1..64})"
  exit 0
fi

# ------------------------------------------------------------------- archiving
# Before anything stops. A snapshot taken after `docker rm` is a snapshot of
# nothing, and the point of this step is that "we can put it back" stays true.
printf "\n${B}Archiving${O}\n"
mkdir -p "$ARCHIVE_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archived=0
archive_failed=0

for src in ${BIND_SOURCES[@]+"${BIND_SOURCES[@]}"}; do
  [[ -d "$src" ]] || { warn "skip" "$src is not a directory"; continue; }
  out="$ARCHIVE_DIR/qdrant-bind-$(echo "$src" | tr -c 'A-Za-z0-9' '-' | sed 's/^-*//;s/-*$//')-$stamp.tar.gz"
  if $SUDO tar czf "$out" -C "$src" . 2>/dev/null; then
    ok "archived" "$src -> $out ($(du -h "$out" | cut -f1))"
    archived=$((archived + 1))
  else
    bad "archive failed" "$src"; archive_failed=1
  fi
done

for vol in ${NAMED_VOLUMES[@]+"${NAMED_VOLUMES[@]}"}; do
  out="$ARCHIVE_DIR/qdrant-volume-$vol-$stamp.tar.gz"
  # busybox tar via the alpine image: no assumption that the host can read
  # /var/lib/docker directly, which it usually cannot without root.
  if docker run --rm -v "$vol":/src:ro -v "$ARCHIVE_DIR":/out alpine:3.20 \
       tar czf "/out/$(basename "$out")" -C /src . >/dev/null 2>&1; then
    ok "archived" "volume $vol -> $out ($(du -h "$out" 2>/dev/null | cut -f1))"
    archived=$((archived + 1))
  else
    bad "archive failed" "volume $vol"; archive_failed=1
  fi
done

if [[ $archive_failed -eq 1 ]]; then
  printf "\n${R}At least one archive failed. Stopping here — nothing has been removed.${O}\n"
  printf "Fix the cause (usually permissions: try with sudo) and run this again.\n"
  exit 1
fi
[[ $archived -eq 0 ]] && warn "archive" "no data found to archive"

# -------------------------------------------------------------------- stopping
printf "\n${B}Stopping${O}\n"

for unit in ${SYSTEMD_UNITS[@]+"${SYSTEMD_UNITS[@]}"}; do
  $SUDO systemctl stop "$unit"    >/dev/null 2>&1 && ok "stopped" "$unit"  || bad "stop failed" "$unit"
  # Disable too, or it is back after the first reboot and the CRM is fighting
  # it for memory with nobody watching.
  $SUDO systemctl disable "$unit" >/dev/null 2>&1 && ok "disabled" "$unit" || warn "disable failed" "$unit"
done

for d in ${COMPOSE_DIRS[@]+"${COMPOSE_DIRS[@]}"}; do
  for f in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
    if [[ -f "$d/$f" ]]; then
      # Without -v the volumes survive, which is the point: this step is
      # reversible until --purge below says otherwise.
      (cd "$d" && docker compose -f "$f" down --remove-orphans >/dev/null 2>&1) \
        && ok "compose down" "$d/$f" || warn "compose down failed" "$d/$f"
      break
    fi
  done
done

for id in ${QDRANT_CONTAINERS[@]+"${QDRANT_CONTAINERS[@]}"}; do
  name=$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's|^/||')
  [[ -z "$name" ]] && continue      # already gone with the compose project
  docker stop "$id" >/dev/null 2>&1 && ok "stopped" "$name" || warn "stop failed" "$name"
  # Removing the container is what actually stops restart=always bringing it
  # back on the next Docker restart. The volume is untouched.
  docker rm "$id"   >/dev/null 2>&1 && ok "removed" "$name" || warn "remove failed" "$name"
done

# ---------------------------------------------------------------------- purging
if [[ $PURGE -eq 1 ]]; then
  printf "\n${B}Purge${O}\n"
  printf "  ${R}This deletes Qdrant's volumes and images.${O}\n"
  printf "  The archive in %s is kept.\n" "$ARCHIVE_DIR"
  read -rp "  Type PURGE to confirm: " answer
  if [[ "$answer" != "PURGE" ]]; then
    warn "purge" "not confirmed — volumes and images kept"
  else
    for vol in ${NAMED_VOLUMES[@]+"${NAMED_VOLUMES[@]}"}; do
      docker volume rm "$vol" >/dev/null 2>&1 && ok "volume deleted" "$vol" \
        || warn "volume not deleted" "$vol (still in use by another container?)"
    done
    while read -r img; do
      [[ -z "$img" ]] && continue
      docker rmi "$img" >/dev/null 2>&1 && ok "image deleted" "$img" \
        || warn "image not deleted" "$img"
    done < <(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -i qdrant || true)
    # Directories are left alone on purpose. ~/qdrant is somebody's working
    # directory as often as it is a deployment, and the tarball above only
    # covers what was mounted into the container.
    for d in ${COMPOSE_DIRS[@]+"${COMPOSE_DIRS[@]}"}; do
      note "left in place: $d — delete it by hand once you are sure"
    done
  fi
fi

# ----------------------------------------------------------------------- after
printf "\n${D}After${O}\n"
if need_docker; then
  left=$(docker ps --format '{{.Names}} ({{.Image}})' 2>/dev/null | grep -i qdrant || true)
  [[ -z "$left" ]] && ok "qdrant running" "no" || bad "qdrant still running" "$left"
fi
if command -v ss >/dev/null 2>&1; then
  for p in 80 443 6333; do
    h=$(port_holder "$p")
    [[ -z "$h" ]] && ok "port $p" "free" || warn "port $p" "held by $h"
  done
fi
free_gb=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
note "free disk now: ${free_gb:-?} GB (preflight wants 40+)"

printf "\n%s\n" "$(printf '=%.0s' {1..64})"
printf "  Archive: %s\n" "$ARCHIVE_DIR"
printf "  Next:\n"
printf "    %s/infra/init-vps-env.sh --yes    # if infra/.env is not filled in yet\n" "$ROOT"
printf "    %s/infra/preflight.sh\n" "$ROOT"
printf "    %s/infra/deploy.sh\n" "$ROOT"
printf "%s\n" "$(printf '=%.0s' {1..64})"
