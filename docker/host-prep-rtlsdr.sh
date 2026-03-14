#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "[ERROR] Run as root: sudo bash docker/host-prep-rtlsdr.sh" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "[ERROR] This helper currently supports Debian/Raspberry Pi OS hosts (apt-get required)." >&2
  exit 1
fi

echo "[INFO] Installing RTL-SDR host utilities and USB tooling..."
apt-get update
apt-get install -y --no-install-recommends rtl-sdr usbutils

echo "[INFO] Installing udev rule for common RTL2832U dongles..."
cat >/etc/udev/rules.d/99-rtlsdr.rules <<'RULES'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE:="0660", GROUP:="plugdev", TAG+="uaccess"
RULES

if ! getent group plugdev >/dev/null; then
  echo "[INFO] Creating plugdev group..."
  groupadd --system plugdev
fi

echo "[INFO] Blacklisting DVB kernel module that conflicts with RTL-SDR..."
cat >/etc/modprobe.d/blacklist-rtlsdr.conf <<'BLACKLIST'
blacklist dvb_usb_rtl28xxu
BLACKLIST

modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
udevadm control --reload-rules
udevadm trigger

echo "[INFO] Host USB devices (checking for Realtek/RTL-SDR):"
lsusb | grep -Ei 'Realtek|RTL|0bda:' || echo "[WARN] RTL-SDR USB device not detected. Ensure dongle is attached."

echo "[INFO] Running rtl_test -t (host validation)..."
if rtl_test -t; then
  echo "[OK] Host RTL-SDR preparation complete."
else
  echo "[WARN] rtl_test failed. Replug the dongle and reboot if this is the first setup."
  exit 1
fi
