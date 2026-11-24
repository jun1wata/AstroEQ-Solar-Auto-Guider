#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCT="AstroEQ Solar Auto Guider"
SERVICE_NAME="astroeq-solar-auto-guider.service"
INSTALL_ROOT="/opt/astroeq-solar-auto-guider"
RUNTIME_DIR="/var/lib/astroeq-solar-auto-guider"
STATE_DIR="/var/lib/astroeq-solar-auto-guider-installer"
STATE_FILE="$STATE_DIR/install-state.env"
UNIT_FILE="/etc/systemd/system/$SERVICE_NAME"
BOOT_CONFIG="/boot/firmware/config.txt"
BACKUP_ROOT="/var/backups/astroeq-solar-auto-guider"
CONFIG_BEGIN="# BEGIN ASTROEQ SOLAR AUTO GUIDER INSTALLER"
CONFIG_END="# END ASTROEQ SOLAR AUTO GUIDER INSTALLER"
UNIT_MARKER="# Managed by AstroEQ Solar Auto Guider install.sh"

STOP_ACTIVE=0
ASSUME_YES=0
KEEP_GROUPS=0
TEMP_DIR=""

log() { printf '[%s uninstall] %s\n' "$PRODUCT" "$*"; }
die() { printf '[%s uninstall] ERROR: %s\n' "$PRODUCT" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo bash uninstall.sh [--stop-active] [--yes] [--keep-groups]

  --stop-active  Stop an active service after an explicit safety confirmation.
  --yes          Confirm --stop-active in non-interactive use.
  --keep-groups  Keep device-access groups added by the installer.

Installed files and runtime data are moved to /var/backups instead of deleted.
Debian packages are not removed because other software may use them.
EOF
}

cleanup() {
  [[ -z "$TEMP_DIR" || ! -d "$TEMP_DIR" ]] || rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT

while (( $# )); do
  case "$1" in
    --stop-active) STOP_ACTIVE=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --keep-groups) KEEP_GROUPS=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "run as root"
[[ -f "$STATE_FILE" ]] || die "installer state is missing: $STATE_FILE"
# shellcheck disable=SC1090
source "$STATE_FILE"

STAMP=$(date +%Y%m%d_%H%M%S)
UNINSTALL_BACKUP="$BACKUP_ROOT/uninstall-$STAMP"
TEMP_DIR=$(mktemp -d -t astroeq-uninstall.XXXXXXXX)

# Validate every managed object before stopping or removing anything.
if [[ -f "$UNIT_FILE" ]]; then
  grep -Fqx "$UNIT_MARKER" "$UNIT_FILE" || die "refusing to remove an unrecognized unit: $UNIT_FILE"
fi
if [[ "${CONFIG_CHANGED:-0}" == 1 ]]; then
  BEGIN_COUNT=$(grep -Fxc "$CONFIG_BEGIN" "$BOOT_CONFIG" || true)
  END_COUNT=$(grep -Fxc "$CONFIG_END" "$BOOT_CONFIG" || true)
  [[ "$BEGIN_COUNT" -eq 1 && "$END_COUNT" -eq 1 ]] || die "managed boot block is missing or duplicated"
  awk -v begin="$CONFIG_BEGIN" -v end="$CONFIG_END" '
    $0 == begin { capture=1 }
    capture { print }
    $0 == end { exit }
  ' "$BOOT_CONFIG" >"$TEMP_DIR/config-block"
  CURRENT_HASH=$(sha256sum "$TEMP_DIR/config-block" | awk '{print $1}')
  [[ -n "${CONFIG_BLOCK_SHA256:-}" && "$CURRENT_HASH" == "$CONFIG_BLOCK_SHA256" ]] || \
    die "managed boot block was modified; refusing to remove it automatically"
fi

if systemctl is-active --quiet "$SERVICE_NAME"; then
  (( STOP_ACTIVE )) || die "$SERVICE_NAME is active; verify that the mount is safe, then rerun with --stop-active"
  if (( ASSUME_YES == 0 )); then
    [[ -t 0 && -t 1 ]] || die "non-interactive stop requires --yes"
    read -r -p "Confirm the mount is stationary and drive power can be cut if needed. Stop the service? [y/N] " answer
    case "$answer" in y|Y|yes|YES) ;; *) die "cancelled" ;; esac
  fi
  log "stopping the active service; this may invoke the stable software's normal stop path"
  systemctl stop "$SERVICE_NAME"
fi

install -d -o root -g root -m 0700 "$UNINSTALL_BACKUP"

if [[ -f "$UNIT_FILE" ]]; then
  systemctl disable "$SERVICE_NAME" >/dev/null
  cp -a -- "$UNIT_FILE" "$UNINSTALL_BACKUP/"
  rm -f -- "$UNIT_FILE"
fi

if [[ "${CONFIG_CHANGED:-0}" == 1 ]]; then
  cp -a -- "$BOOT_CONFIG" "$UNINSTALL_BACKUP/config.txt.before-uninstall"
  awk -v begin="$CONFIG_BEGIN" -v end="$CONFIG_END" '
    $0 == begin { skip=1; next }
    $0 == end { skip=0; next }
    !skip { print }
  ' "$BOOT_CONFIG" >"$TEMP_DIR/config.txt"
  cat "$TEMP_DIR/config.txt" >"$BOOT_CONFIG"
  log "removed only the installer-managed boot block"
fi

if [[ "${GPSD_ENABLED_BY_INSTALLER:-0}" == 1 ]]; then
  systemctl disable gpsd.socket >/dev/null || true
fi

if (( KEEP_GROUPS == 0 )); then
  for group in ${ADDED_GROUPS:-}; do
    if getent group "$group" >/dev/null && id -nG "$TARGET_USER" | tr ' ' '\n' | grep -Fxq "$group"; then
      gpasswd -d "$TARGET_USER" "$group" >/dev/null
      log "removed installer-added group membership: $TARGET_USER -> $group"
    fi
  done
fi

[[ ! -e "$INSTALL_ROOT" ]] || mv -- "$INSTALL_ROOT" "$UNINSTALL_BACKUP/install-root"
[[ ! -e "$RUNTIME_DIR" ]] || mv -- "$RUNTIME_DIR" "$UNINSTALL_BACKUP/runtime-data"
[[ ! -e "$STATE_DIR" ]] || mv -- "$STATE_DIR" "$UNINSTALL_BACKUP/installer-state"

systemctl daemon-reload
systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true

trap - EXIT
cleanup

cat <<EOF
$PRODUCT was disabled and its managed settings were removed.
Recoverable files and runtime data were moved to:
  $UNINSTALL_BACKUP

Installed Debian packages were intentionally kept.
Reboot to apply the boot configuration change.
EOF
