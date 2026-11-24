#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCT="AstroEQ Solar Auto Guider"
SERVICE_NAME="astroeq-solar-auto-guider.service"
INSTALL_ROOT="/opt/astroeq-solar-auto-guider"
INSTALL_SRC="$INSTALL_ROOT/src"
INSTALL_SCRIPTS="$INSTALL_ROOT/scripts"
VENV="$INSTALL_ROOT/venv"
RUNTIME_DIR="/var/lib/astroeq-solar-auto-guider"
STATE_DIR="/var/lib/astroeq-solar-auto-guider-installer"
STATE_FILE="$STATE_DIR/install-state.env"
UNIT_FILE="/etc/systemd/system/$SERVICE_NAME"
BOOT_CONFIG="/boot/firmware/config.txt"
BACKUP_ROOT="/var/backups/astroeq-solar-auto-guider"
MANAGED_MARKER="$INSTALL_ROOT/.installer-managed"
CONFIG_BEGIN="# BEGIN ASTROEQ SOLAR AUTO GUIDER INSTALLER"
CONFIG_END="# END ASTROEQ SOLAR AUTO GUIDER INSTALLER"
UNIT_MARKER="# Managed by AstroEQ Solar Auto Guider install.sh"
MIN_FREE_KIB=1048576

LUMA_CORE_VERSION="2.4.2"
LUMA_OLED_VERSION="3.16.0"
LUMA_CORE_WHEEL_SHA256="5eccbbdfde4b8cd9113d1bd355be6a29041c0b2e7f56e1873a4eed356e9fc582"
LUMA_OLED_WHEEL_SHA256="2d863b759472b41ef3a75ddb9532100ce1d26042707d43472b5687b673e4e523"

SOURCE_FILES=(
  astroeq_camera_calibration_halfarcsec.py
  astroeq_guide_quality.py
  astroeq_guide_socket.py
  astroeq_pulse_autoguide.py
  astroeq_remote_mount.py
  astroeq_startup_oled.py
  astroeq_sun_position.py
  astroeq_sun_tracking_generic_ch340.py
  astroeq_sun_tracking_guided.py
  astroeq_tracking_relative_goto_1arcsec.py
  oled_ui_camera_calibration_1arcsec_dev.py
  oled_ui_dev.py
  run_solar_goto_with_oled.py
  solar_center_oled_modes_right.py
  ssd1315_encoder.py
  ssd1315_oled_modes_right.py
)

APT_PACKAGES=(
  python3-opencv python3-numpy python3-pil python3-serial
  python3-gpiozero python3-evdev python3-lgpio
  python3-luma.oled python3-luma.core python3-gps
  python3-venv gpsd gpsd-clients v4l-utils i2c-tools usbutils
)

REQUIRED_CONFIG_LINES=(
  "dtparam=i2c_arm=on"
  "dtoverlay=dwc2,dr_mode=host"
  "dtoverlay=rotary-encoder,pin_a=5,pin_b=6,relative_axis=1,steps-per-period=2"
)

TARGET_USER_OVERRIDE=""
AUTO_REBOOT=0
ASSUME_YES=0
TEMP_DIR=""
CURRENT_STEP="initial checks"

log() { printf '[%s] %s\n' "$PRODUCT" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$PRODUCT" "$*" >&2; }
die() { printf '[%s] ERROR: %s\n' "$PRODUCT" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo bash install.sh [--user USER] [--reboot] [--yes]

  --user USER  Service account. Required when not invoked through sudo.
  --reboot     Reboot automatically after a successful non-interactive install.
  --yes        Accept non-destructive installer prompts; does not imply reboot.
  --help       Show this help.
EOF
}

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}

on_exit() {
  local rc=$?
  cleanup
  if (( rc != 0 )); then
    printf '[%s] FAILED during: %s (line %s).\n' "$PRODUCT" "$CURRENT_STEP" "${BASH_LINENO[0]:-unknown}" >&2
    printf '[%s] The system was not rebooted and the service was not started.\n' "$PRODUCT" >&2
  fi
  exit "$rc"
}
trap on_exit EXIT

while (( $# )); do
  case "$1" in
    --user)
      (( $# >= 2 )) || die "--user requires an account name"
      TARGET_USER_OVERRIDE=$2
      shift 2
      ;;
    --reboot) AUTO_REBOOT=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "run this installer as root: sudo bash install.sh"
for command_name in apt-get dpkg getent systemctl systemd-analyze sha256sum find awk grep; do
  command -v "$command_name" >/dev/null || die "required command is unavailable: $command_name"
done

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -f "$SCRIPT_DIR/README.md" && -f "$SCRIPT_DIR/README_JA.md" ]] || \
  die "run install.sh from the repository root"

CURRENT_STEP="validating repository input"
for file in "${SOURCE_FILES[@]}"; do
  [[ -f "$SCRIPT_DIR/src/$file" ]] || die "missing stable source: src/$file"
done
[[ -f "$SCRIPT_DIR/scripts/verify_install.sh" ]] || die "missing scripts/verify_install.sh"
[[ -f "$SCRIPT_DIR/scripts/uninstall.sh" ]] || die "missing scripts/uninstall.sh"

CURRENT_STEP="detecting the service account"
if [[ -n "$TARGET_USER_OVERRIDE" ]]; then
  TARGET_USER=$TARGET_USER_OVERRIDE
elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]]; then
  TARGET_USER=$SUDO_USER
else
  die "could not identify a non-root account; use --user USER"
fi
id "$TARGET_USER" >/dev/null 2>&1 || die "account does not exist: $TARGET_USER"
[[ $(id -u "$TARGET_USER") -ne 0 ]] || die "the service account must not be root"
REQUESTED_USER=$TARGET_USER

PREVIOUS_ADDED_GROUPS=""
GPSD_ENABLED_BY_INSTALLER=0
CONFIG_CHANGED=0
CONFIG_BACKUP=""
CONFIG_BLOCK_SHA256=""
if [[ -f "$STATE_FILE" ]]; then
  # The state file is root-owned and mode 0600 when created by this installer.
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  STATE_TARGET_USER=${TARGET_USER:-}
  [[ "$STATE_TARGET_USER" == "$REQUESTED_USER" ]] || \
    die "existing installation uses $STATE_TARGET_USER; rerun with --user $STATE_TARGET_USER"
  PREVIOUS_ADDED_GROUPS=${ADDED_GROUPS:-}
  GPSD_ENABLED_BY_INSTALLER=${GPSD_ENABLED_BY_INSTALLER:-0}
  CONFIG_CHANGED=${CONFIG_CHANGED:-0}
  CONFIG_BACKUP=${CONFIG_BACKUP:-}
  CONFIG_BLOCK_SHA256=${CONFIG_BLOCK_SHA256:-}
fi
TARGET_USER=$REQUESTED_USER
TARGET_GROUP=$(id -gn "$TARGET_USER")
TARGET_HOME=$(getent passwd "$TARGET_USER" | awk -F: '{print $6}')
[[ "$TARGET_HOME" = /* && "$TARGET_HOME" != *$'\n'* && "$TARGET_HOME" != *'"'* ]] || \
  die "invalid home directory for $TARGET_USER"

CURRENT_STEP="checking Raspberry Pi model and operating system"
[[ -r /proc/device-tree/model ]] || die "Raspberry Pi model information is unavailable"
MODEL=$(tr -d '\0' </proc/device-tree/model)
[[ "$MODEL" == *"Raspberry Pi Zero 2 W"* ]] || die "unsupported hardware: $MODEL"
[[ $(dpkg --print-architecture) == arm64 ]] || die "Debian architecture must be arm64"
[[ $(uname -m) == aarch64 || $(uname -m) == arm64 ]] || die "kernel architecture must be ARM64"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${VERSION_CODENAME:-}" == trixie && "${VERSION_ID:-}" == 13* ]] || \
  die "Debian 13 / trixie is required (found ${PRETTY_NAME:-unknown})"

CURRENT_STEP="checking disk space"
DISK_TARGET=/
[[ -d /opt ]] && DISK_TARGET=/opt
FREE_KIB=$(df -Pk "$DISK_TARGET" | awk 'NR==2 {print $4}')
[[ "$FREE_KIB" =~ ^[0-9]+$ && "$FREE_KIB" -ge "$MIN_FREE_KIB" ]] || \
  die "at least 1 GiB free space is required on $DISK_TARGET"

CURRENT_STEP="checking apt configuration"
command -v apt-get >/dev/null || die "apt-get is required"
APT_SOURCE_FOUND=0
while IFS= read -r source_file; do
  [[ -f "$source_file" ]] || continue
  if grep -Eq '^[[:space:]]*(deb[[:space:]]|Types:[[:space:]].*deb)' "$source_file"; then
    APT_SOURCE_FOUND=1
    break
  fi
done < <(find /etc/apt -maxdepth 2 -type f \( -name 'sources.list' -o -name '*.list' -o -name '*.sources' \) -print)
(( APT_SOURCE_FOUND == 1 )) || die "no enabled apt repository definition was found"

CURRENT_STEP="checking existing installations and services"
if [[ -e "$INSTALL_ROOT" && ! -f "$MANAGED_MARKER" ]]; then
  die "$INSTALL_ROOT exists but is not managed by this installer"
fi
if [[ -e "$UNIT_FILE" ]] && ! grep -Fqx "$UNIT_MARKER" "$UNIT_FILE"; then
  die "$UNIT_FILE exists but is not managed by this installer"
fi
if systemctl is-active --quiet astroeq-sun.service; then
  die "legacy astroeq-sun.service is active; stop it safely before installing"
fi

SERVICE_ACTIVE=0
systemctl is-active --quiet "$SERVICE_NAME" && SERVICE_ACTIVE=1
if (( SERVICE_ACTIVE )); then
  [[ -f "$MANAGED_MARKER" ]] || die "an unrecognized $SERVICE_NAME is active"
  for file in "${SOURCE_FILES[@]}"; do
    cmp -s "$SCRIPT_DIR/src/$file" "$INSTALL_SRC/$file" || \
      die "$SERVICE_NAME is active and installed source differs: $file; stop it safely first"
  done
fi

TEMP_DIR=$(mktemp -d -t astroeq-installer.XXXXXXXX)
UNIT_CANDIDATE="$TEMP_DIR/$SERVICE_NAME"
cat >"$UNIT_CANDIDATE" <<EOF
$UNIT_MARKER
[Unit]
Description=AstroEQ Solar Auto Guider
Wants=gpsd.socket
After=gpsd.socket systemd-udev-trigger.service

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$RUNTIME_DIR
Environment="HOME=$TARGET_HOME"
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$VENV/bin/python -u $RUNTIME_DIR/run_solar_goto_with_oled.py --ra-west-dir 0 --dec-south-dir 1 --enable-guide-after-cal --cal-drift off
Restart=no
UMask=0022

[Install]
WantedBy=multi-user.target
EOF

if (( SERVICE_ACTIVE )) && ! cmp -s "$UNIT_CANDIDATE" "$UNIT_FILE"; then
  die "$SERVICE_NAME is active and its unit would change; stop it safely first"
fi

CURRENT_STEP="checking boot configuration for conflicts"
[[ -f "$BOOT_CONFIG" ]] || die "$BOOT_CONFIG does not exist"
BEGIN_COUNT=$(grep -Fxc "$CONFIG_BEGIN" "$BOOT_CONFIG" || true)
END_COUNT=$(grep -Fxc "$CONFIG_END" "$BOOT_CONFIG" || true)
[[ "$BEGIN_COUNT" -eq "$END_COUNT" && "$BEGIN_COUNT" -le 1 ]] || \
  die "incomplete or duplicate installer markers in $BOOT_CONFIG"
if (( BEGIN_COUNT == 1 )) && [[ ! -f "$STATE_FILE" ]]; then
  die "managed boot block exists but installer state is missing"
fi

mapfile -t CONFIG_ENTRIES < <(
  awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    /^[[:space:]]*\[/ { section=$0; gsub(/[[:space:]]/, "", section); next }
    {
      line=$0
      sub(/[[:space:]]+#.*$/, "", line)
      gsub(/[[:space:]]/, "", line)
      if (line ~ /^dtparam=i2c_arm=/ || line ~ /^dtoverlay=dwc2([,=]|$)/ || line ~ /^dtoverlay=rotary-encoder([,=]|$)/)
        print section "\t" line
    }
  ' "$BOOT_CONFIG"
)

MISSING_CONFIG_LINES=()
for required in "${REQUIRED_CONFIG_LINES[@]}"; do
  found=0
  key=${required%%=*}
  [[ "$required" == dtoverlay=* ]] && key=${required%%,*}
  for entry in "${CONFIG_ENTRIES[@]}"; do
    section=${entry%%$'\t'*}
    line=${entry#*$'\t'}
    if [[ "$line" == "$required" ]]; then
      [[ -z "$section" || "$section" == "[all]" ]] || \
        die "$required is inside conditional section $section; review manually"
      found=1
    elif [[ "$line" == "$key"* ]]; then
      die "conflicting boot setting: $line"
    fi
  done
  (( found )) || MISSING_CONFIG_LINES+=("$required")
done

CURRENT_STEP="updating apt indexes (network and repository check)"
apt-get update

CURRENT_STEP="installing Debian dependencies"
if (( SERVICE_ACTIVE )); then
  for package in "${APT_PACKAGES[@]}"; do
    dpkg-query -W -f='${db:Status-Status}\n' "$package" 2>/dev/null | grep -Fxq 'installed' || \
      die "$SERVICE_NAME is active and dependency is missing: $package; stop it safely first"
  done
  log "active managed service detected; all Debian dependencies are present, so apt install is skipped"
else
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"
fi

CURRENT_STEP="creating the isolated Python environment"
install -d -o root -g root -m 0755 "$INSTALL_ROOT" "$INSTALL_SRC" "$INSTALL_SCRIPTS" "$INSTALL_ROOT/share"
if [[ ! -f "$MANAGED_MARKER" ]]; then
  printf 'Managed by install.sh; use scripts/uninstall.sh for removal.\n' >"$MANAGED_MARKER"
  chown root:root "$MANAGED_MARKER"
  chmod 0644 "$MANAGED_MARKER"
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv --system-site-packages "$VENV"
elif ! grep -Eq '^include-system-site-packages[[:space:]]*=[[:space:]]*true$' "$VENV/pyvenv.cfg"; then
  die "existing venv does not use system site packages: $VENV"
fi

current_luma_versions() {
  "$VENV/bin/python" -B - <<'PY'
from importlib.metadata import version
print(version("luma.core"), version("luma.oled"))
PY
}

LUMA_VERSIONS=$(current_luma_versions 2>/dev/null || true)
if [[ "$LUMA_VERSIONS" != "$LUMA_CORE_VERSION $LUMA_OLED_VERSION" ]]; then
  (( SERVICE_ACTIVE == 0 )) || die "active service uses unexpected luma versions: ${LUMA_VERSIONS:-missing}"
  WHEEL_DIR="$TEMP_DIR/wheels"
  mkdir -p "$WHEEL_DIR"
  "$VENV/bin/python" -m pip download --only-binary=:all: --no-deps --dest "$WHEEL_DIR" \
    "luma.core==$LUMA_CORE_VERSION" "luma.oled==$LUMA_OLED_VERSION"
  CORE_WHEEL=$(find "$WHEEL_DIR" -maxdepth 1 -type f -iname 'luma_core-2.4.2-*.whl' -print -quit)
  OLED_WHEEL=$(find "$WHEEL_DIR" -maxdepth 1 -type f -iname 'luma_oled-3.16.0-*.whl' -print -quit)
  [[ -n "$CORE_WHEEL" && -n "$OLED_WHEEL" ]] || die "expected luma wheels were not downloaded"
  [[ $(sha256sum "$CORE_WHEEL" | awk '{print $1}') == "$LUMA_CORE_WHEEL_SHA256" ]] || \
    die "luma.core wheel checksum mismatch"
  [[ $(sha256sum "$OLED_WHEEL" | awk '{print $1}') == "$LUMA_OLED_WHEEL_SHA256" ]] || \
    die "luma.oled wheel checksum mismatch"
  "$VENV/bin/python" -m pip install --no-index --no-deps "$CORE_WHEEL" "$OLED_WHEEL"
fi

CURRENT_STEP="installing the stable source files"
if (( SERVICE_ACTIVE == 0 )); then
  for file in "${SOURCE_FILES[@]}"; do
    install -o root -g root -m 0644 "$SCRIPT_DIR/src/$file" "$INSTALL_SRC/$file"
  done
fi
install -o root -g root -m 0755 "$SCRIPT_DIR/scripts/verify_install.sh" "$INSTALL_SCRIPTS/verify_install.sh"
install -o root -g root -m 0755 "$SCRIPT_DIR/scripts/uninstall.sh" "$INSTALL_SCRIPTS/uninstall.sh"
(
  cd "$INSTALL_SRC"
  sha256sum "${SOURCE_FILES[@]}"
) >"$TEMP_DIR/source.sha256"
install -o root -g root -m 0644 "$TEMP_DIR/source.sha256" "$INSTALL_ROOT/share/source.sha256"

CURRENT_STEP="preparing the runtime directory"
install -d -o root -g "$TARGET_GROUP" -m 1775 "$RUNTIME_DIR"
for file in "${SOURCE_FILES[@]}"; do
  runtime_path="$RUNTIME_DIR/$file"
  if [[ -e "$runtime_path" && ! -L "$runtime_path" ]]; then
    die "runtime path is not an installer-managed symlink: $runtime_path"
  fi
  if [[ -L "$runtime_path" && $(readlink -f "$runtime_path") == "$INSTALL_SRC/$file" ]]; then
    continue
  fi
  (( SERVICE_ACTIVE == 0 )) || die "$SERVICE_NAME is active and runtime link differs: $file"
  ln -sfn "$INSTALL_SRC/$file" "$runtime_path"
  chown -h root:root "$runtime_path"
done

CURRENT_STEP="checking Python imports and installed versions"
HOME="$TARGET_HOME" PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -B - <<PY
import ast
import importlib
from importlib.metadata import version
from pathlib import Path

expected = {"luma.core": "$LUMA_CORE_VERSION", "luma.oled": "$LUMA_OLED_VERSION"}
for distribution, wanted in expected.items():
    actual = version(distribution)
    if actual != wanted:
        raise SystemExit(f"{distribution}: expected {wanted}, found {actual}")

for module in ("cv2", "numpy", "PIL", "serial", "gpiozero", "evdev", "lgpio", "gps",
               "luma.core", "luma.oled.device"):
    importlib.import_module(module)
from luma.oled.device import sh1107

for path in Path("$INSTALL_SRC").glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("Python imports, luma versions, SH1107 class, and source syntax: OK")
PY

CURRENT_STEP="adding device-access groups"
NEW_GROUPS=()
for group in dialout video input gpio i2c; do
  getent group "$group" >/dev/null || { warn "group does not exist and was skipped: $group"; continue; }
  if ! id -nG "$TARGET_USER" | tr ' ' '\n' | grep -Fxq "$group"; then
    usermod -a -G "$group" "$TARGET_USER"
    NEW_GROUPS+=("$group")
  fi
done

ADDED_GROUPS=$PREVIOUS_ADDED_GROUPS
for group in "${NEW_GROUPS[@]}"; do
  case " $ADDED_GROUPS " in
    *" $group "*) ;;
    *) ADDED_GROUPS="${ADDED_GROUPS:+$ADDED_GROUPS }$group" ;;
  esac
done

CURRENT_STEP="backing up and updating boot configuration"
if (( ${#MISSING_CONFIG_LINES[@]} > 0 )); then
  (( BEGIN_COUNT == 0 )) || die "managed boot block exists but required lines are missing"
  install -d -o root -g root -m 0755 "$BACKUP_ROOT"
  STAMP=$(date +%Y%m%d_%H%M%S)
  CONFIG_BACKUP="$BACKUP_ROOT/config.txt.$STAMP.bak"
  cp -a -- "$BOOT_CONFIG" "$CONFIG_BACKUP"
  {
    printf '\n%s\n' "$CONFIG_BEGIN"
    printf '[all]\n'
    printf '%s\n' "${MISSING_CONFIG_LINES[@]}"
    printf '%s\n' "$CONFIG_END"
  } >>"$BOOT_CONFIG"
  CONFIG_CHANGED=1
  awk -v begin="$CONFIG_BEGIN" -v end="$CONFIG_END" '
    $0 == begin { capture=1 }
    capture { print }
    $0 == end { exit }
  ' "$BOOT_CONFIG" >"$TEMP_DIR/config-block"
  CONFIG_BLOCK_SHA256=$(sha256sum "$TEMP_DIR/config-block" | awk '{print $1}')
elif (( BEGIN_COUNT == 1 )); then
  awk -v begin="$CONFIG_BEGIN" -v end="$CONFIG_END" '
    $0 == begin { capture=1 }
    capture { print }
    $0 == end { exit }
  ' "$BOOT_CONFIG" >"$TEMP_DIR/config-block"
  CURRENT_BLOCK_SHA256=$(sha256sum "$TEMP_DIR/config-block" | awk '{print $1}')
  [[ -z "$CONFIG_BLOCK_SHA256" || "$CURRENT_BLOCK_SHA256" == "$CONFIG_BLOCK_SHA256" ]] || \
    die "managed boot configuration block was modified; review it manually"
  CONFIG_CHANGED=1
  CONFIG_BLOCK_SHA256=$CURRENT_BLOCK_SHA256
fi

CURRENT_STEP="installing and validating the systemd unit"
if [[ ! -f "$UNIT_FILE" ]] || ! cmp -s "$UNIT_CANDIDATE" "$UNIT_FILE"; then
  install -o root -g root -m 0644 "$UNIT_CANDIDATE" "$UNIT_FILE"
  systemctl daemon-reload
fi
systemd-analyze verify "$UNIT_FILE"

if ! systemctl is-enabled --quiet gpsd.socket; then
  systemctl enable gpsd.socket
  GPSD_ENABLED_BY_INSTALLER=1
fi
systemctl enable "$SERVICE_NAME"

CURRENT_STEP="recording installer state"
install -d -o root -g root -m 0700 "$STATE_DIR"
STATE_TMP="$TEMP_DIR/install-state.env"
{
  printf 'STATE_VERSION=%q\n' 1
  printf 'TARGET_USER=%q\n' "$TARGET_USER"
  printf 'TARGET_GROUP=%q\n' "$TARGET_GROUP"
  printf 'TARGET_HOME=%q\n' "$TARGET_HOME"
  printf 'ADDED_GROUPS=%q\n' "$ADDED_GROUPS"
  printf 'GPSD_ENABLED_BY_INSTALLER=%q\n' "$GPSD_ENABLED_BY_INSTALLER"
  printf 'CONFIG_CHANGED=%q\n' "$CONFIG_CHANGED"
  printf 'CONFIG_BACKUP=%q\n' "$CONFIG_BACKUP"
  printf 'CONFIG_BLOCK_SHA256=%q\n' "$CONFIG_BLOCK_SHA256"
  printf 'INSTALLED_AT=%q\n' "$(date --iso-8601=seconds)"
} >"$STATE_TMP"
install -o root -g root -m 0600 "$STATE_TMP" "$STATE_FILE"

CURRENT_STEP="running the non-hardware installation verifier"
bash "$INSTALL_SCRIPTS/verify_install.sh"

trap - EXIT
cleanup

cat <<EOF

$PRODUCT installation completed successfully.

The service is enabled but was not started by the installer.
No AstroEQ serial connection or motor command was issued.

After reboot, diagnose the installation with:
  sudo bash $INSTALL_SCRIPTS/verify_install.sh --post-reboot --hardware

Review service logs with:
  journalctl -u $SERVICE_NAME -b --no-pager
EOF

if (( AUTO_REBOOT )); then
  log "reboot requested with --reboot"
  systemctl reboot
elif [[ -t 0 && -t 1 ]]; then
  if (( ASSUME_YES )); then
    log "--yes does not imply reboot; run 'sudo reboot' when ready"
  else
    read -r -p "Reboot now? [y/N] " answer
    case "$answer" in
      y|Y|yes|YES) systemctl reboot ;;
      *) log "reboot skipped; run 'sudo reboot' when ready" ;;
    esac
  fi
else
  log "non-interactive run: reboot skipped (use --reboot to request it)"
fi
