#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="astroeq-solar-auto-guider.service"
INSTALL_ROOT="/opt/astroeq-solar-auto-guider"
INSTALL_SRC="$INSTALL_ROOT/src"
VENV="$INSTALL_ROOT/venv"
RUNTIME_DIR="/var/lib/astroeq-solar-auto-guider"
STATE_FILE="/var/lib/astroeq-solar-auto-guider-installer/install-state.env"
UNIT_FILE="/etc/systemd/system/$SERVICE_NAME"
BOOT_CONFIG="/boot/firmware/config.txt"
LUMA_CORE_VERSION="2.4.2"
LUMA_OLED_VERSION="3.16.0"

SOURCE_FILES=(
  astroeq_camera_calibration_halfarcsec.py astroeq_guide_quality.py
  astroeq_guide_socket.py astroeq_pulse_autoguide.py astroeq_remote_mount.py
  astroeq_startup_oled.py astroeq_sun_position.py
  astroeq_sun_tracking_generic_ch340.py astroeq_sun_tracking_guided.py
  astroeq_tracking_relative_goto_1arcsec.py
  oled_ui_camera_calibration_1arcsec_dev.py oled_ui_dev.py
  run_solar_goto_with_oled.py solar_center_oled_modes_right.py
  ssd1315_encoder.py ssd1315_oled_modes_right.py
)

HARDWARE=0
POST_REBOOT=0
FAILURES=0
WARNINGS=0

usage() {
  cat <<'EOF'
Usage: sudo bash verify_install.sh [--hardware] [--post-reboot]

  --hardware     Check device presence without sending AstroEQ commands.
  --post-reboot  Require the enabled service to be active after reboot.
EOF
}

while (( $# )); do
  case "$1" in
    --hardware) HARDWARE=1; shift ;;
    --post-reboot) POST_REBOOT=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

pass() { printf '[PASS] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '[FAIL] %s\n' "$*"; FAILURES=$((FAILURES + 1)); }

if [[ ! -f "$STATE_FILE" ]]; then
  fail "installer state is missing: $STATE_FILE"
  printf 'Verification failed.\n'
  exit 1
fi
# shellcheck disable=SC1090
source "$STATE_FILE"

if [[ -r /proc/device-tree/model ]]; then
  MODEL=$(tr -d '\0' </proc/device-tree/model)
  if [[ "$MODEL" == *"Raspberry Pi Zero 2 W"* ]]; then
    pass "hardware model: $MODEL"
  else
    fail "unsupported model: $MODEL"
  fi
else
  fail "Raspberry Pi model information is unavailable"
fi

if [[ $(dpkg --print-architecture 2>/dev/null || true) == arm64 ]]; then
  pass "Debian architecture: arm64"
else
  fail "Debian architecture is not arm64"
fi
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${VERSION_CODENAME:-}" == trixie && "${VERSION_ID:-}" == 13* ]]; then
    pass "operating system: ${PRETTY_NAME:-Debian 13}"
  else
    fail "operating system is not Debian 13 / trixie"
  fi
else
  fail "/etc/os-release is unavailable"
fi

if [[ -x "$VENV/bin/python" ]]; then
  pass "isolated Python environment exists"
else
  fail "venv Python is missing"
fi
[[ -f "$INSTALL_ROOT/share/source.sha256" ]] || fail "source checksum manifest is missing"
if [[ -f "$INSTALL_ROOT/share/source.sha256" ]]; then
  if (cd "$INSTALL_SRC" && sha256sum -c "$INSTALL_ROOT/share/source.sha256" >/dev/null); then
    pass "all 16 installed source checksums match"
  else
    fail "installed source checksum mismatch"
  fi
fi

LINKS_OK=1
for file in "${SOURCE_FILES[@]}"; do
  if [[ ! -L "$RUNTIME_DIR/$file" || $(readlink -f "$RUNTIME_DIR/$file") != "$INSTALL_SRC/$file" ]]; then
    fail "runtime link mismatch: $file"
    LINKS_OK=0
  fi
done
if (( LINKS_OK )); then
  pass "runtime links point to the protected source directory"
fi

if [[ -x "$VENV/bin/python" ]]; then
  PYTHON_CHECK=$(HOME="${TARGET_HOME:-/tmp}" PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -B - <<PY 2>&1
import ast
import importlib
from importlib.metadata import version
from pathlib import Path

for module in ("cv2", "numpy", "PIL", "serial", "gpiozero", "evdev", "lgpio", "gps",
               "luma.core", "luma.oled.device"):
    importlib.import_module(module)
from luma.oled.device import sh1107
assert version("luma.core") == "$LUMA_CORE_VERSION"
assert version("luma.oled") == "$LUMA_OLED_VERSION"
for path in Path("$INSTALL_SRC").glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("OK")
PY
  ) || true
  if [[ "$PYTHON_CHECK" == *OK* ]]; then
    pass "imports, luma versions, SH1107 class, and Python syntax"
  else
    fail "Python verification failed: $PYTHON_CHECK"
  fi
fi

CONFIG_OK=1
for required in \
  'dtparam=i2c_arm=on' \
  'dtoverlay=dwc2,dr_mode=host' \
  'dtoverlay=rotary-encoder,pin_a=5,pin_b=6,relative_axis=1,steps-per-period=2'; do
  if ! awk -v wanted="$required" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    { line=$0; sub(/[[:space:]]+#.*$/, "", line); gsub(/[[:space:]]/, "", line) }
    line == wanted { found=1 }
    END { exit !found }
  ' "$BOOT_CONFIG"; then
    fail "boot setting is missing: $required"
    CONFIG_OK=0
  fi
done
if (( CONFIG_OK )); then
  pass "I2C, USB host, and rotary encoder boot settings"
fi

if [[ -f "$UNIT_FILE" ]]; then
  if grep -Fqx 'Restart=no' "$UNIT_FILE"; then
    pass "systemd Restart=no is preserved"
  else
    fail "systemd unit does not contain Restart=no"
  fi
  if grep -Fqx "User=${TARGET_USER:-}" "$UNIT_FILE"; then
    pass "systemd service account: ${TARGET_USER:-unknown}"
  else
    fail "systemd service account mismatch"
  fi
  if grep -Fq -- '--ra-west-dir 0 --dec-south-dir 1 --enable-guide-after-cal --cal-drift off' "$UNIT_FILE"; then
    pass "systemd launch arguments match the published configuration"
  else
    fail "systemd launch arguments mismatch"
  fi
  if systemd-analyze verify "$UNIT_FILE" >/dev/null 2>&1; then
    pass "systemd unit syntax"
  else
    fail "systemd unit verification failed"
  fi
else
  fail "systemd unit is missing"
fi

if systemctl is-enabled --quiet "$SERVICE_NAME"; then
  pass "$SERVICE_NAME is enabled"
else
  fail "$SERVICE_NAME is not enabled"
fi
if systemctl is-enabled --quiet gpsd.socket; then
  pass "gpsd.socket is enabled"
else
  warn "gpsd.socket is not enabled"
fi

if (( POST_REBOOT )); then
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    pass "$SERVICE_NAME is active after reboot"
  else
    fail "$SERVICE_NAME is not active after reboot"
    systemctl status --no-pager "$SERVICE_NAME" || true
  fi
fi

if (( HARDWARE )); then
  if [[ -e /dev/i2c-1 ]]; then
    pass "I2C bus /dev/i2c-1 exists"
  else
    warn "I2C bus /dev/i2c-1 is not available"
  fi
  if [[ -e /dev/i2c-1 && ${EUID:-$(id -u)} -eq 0 ]] && command -v i2cdetect >/dev/null; then
    OLED_SCAN=$(i2cdetect -y 1 0x3c 0x3c 2>/dev/null || true)
    if [[ "$OLED_SCAN" == *"3c"* || "$OLED_SCAN" == *"UU"* ]]; then
      pass "OLED responds or is claimed at I2C address 0x3c"
    else
      warn "OLED was not detected at I2C address 0x3c"
    fi
  fi

  if command -v lsusb >/dev/null; then
    if lsusb -d 1546:01a7 >/dev/null 2>&1; then
      pass "known USB GPS is present"
    else
      warn "known USB GPS is not present"
    fi
    if lsusb -d 1a86:7523 >/dev/null 2>&1 || lsusb -d 2341:0042 >/dev/null 2>&1; then
      pass "a published AstroEQ USB interface is present (not opened)"
    else
      warn "a published AstroEQ USB interface is not present"
    fi
  else
    warn "lsusb is unavailable"
  fi

  if [[ -e /dev/video0 ]]; then
    pass "guide camera node /dev/video0 exists"
  else
    warn "guide camera node /dev/video0 is not present"
  fi

  if [[ -x "$VENV/bin/python" ]]; then
    ENCODERS=$(PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -B - <<'PY' 2>/dev/null || true
from evdev import InputDevice, list_devices
for path in list_devices():
    device = InputDevice(path)
    try:
        print(device.name)
    finally:
        device.close()
PY
    )
    if [[ "$ENCODERS" == *"rotary@5"* ]]; then
      pass "rotary@5 input device is present"
    else
      warn "rotary@5 input device is not present or not readable"
    fi
  fi
fi

printf '\nVerification summary: %d failure(s), %d warning(s).\n' "$FAILURES" "$WARNINGS"
printf 'No AstroEQ serial device was opened and no motor command was sent.\n'
(( FAILURES == 0 ))
