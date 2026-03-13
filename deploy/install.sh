#!/usr/bin/env bash
# install.sh — Pi-side installation script for rf-monitor
#
# Run ON the Raspberry Pi after deploy.sh has synced the source.
# Requires: sudo privileges, Python 3.8+, apt package manager.
#
# Usage:
#   sudo bash install.sh          # Full install (app + systemd + udev)
#   sudo bash install.sh --app    # App only (venv + pip install)
#   sudo bash install.sh --uninstall

set -euo pipefail

APP_NAME="rf-monitor"
INSTALL_DIR="/opt/rf-monitor"
VENV_DIR="${INSTALL_DIR}/venv"
SERVICE_USER="rf-monitor"
SERVICE_GROUP="rf-monitor"
LOG_DIR="/var/log/rf-monitor"
SCAN_DIR="${LOG_DIR}/scans"
CONFIG_DIR="/etc/rf-monitor"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
err()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        err "This script must be run as root (sudo)."
        exit 1
    fi
}

# --------------------------------------------------------------------------
# System Dependencies
# --------------------------------------------------------------------------

install_system_deps() {
    info "Installing system dependencies..."
    apt-get update -qq
    apt-get install -y -qq \
        python3 \
        python3-venv \
        python3-pip \
        rtl-sdr \
        librtlsdr-dev \
        libatlas-base-dev \
        > /dev/null 2>&1
    ok "System dependencies installed."
}

# --------------------------------------------------------------------------
# Blacklist DVB Kernel Driver
# --------------------------------------------------------------------------

blacklist_dvb_driver() {
    local blacklist_file="/etc/modprobe.d/blacklist-rtlsdr.conf"
    if [[ ! -f "${blacklist_file}" ]]; then
        info "Blacklisting kernel DVB driver for RTL-SDR..."
        echo "blacklist dvb_usb_rtl28xxu" > "${blacklist_file}"
        modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
        ok "DVB driver blacklisted. Reconnect RTL-SDR USB device."
    else
        ok "DVB driver already blacklisted."
    fi
}

# --------------------------------------------------------------------------
# Service User
# --------------------------------------------------------------------------

create_service_user() {
    if ! id -u "${SERVICE_USER}" &>/dev/null; then
        info "Creating service user '${SERVICE_USER}'..."
        useradd --system --shell /usr/sbin/nologin \
            --home-dir "${INSTALL_DIR}" \
            --comment "RF Monitor Service" \
            "${SERVICE_USER}"
        usermod -aG plugdev "${SERVICE_USER}"
        ok "Service user created."
    else
        ok "Service user '${SERVICE_USER}' already exists."
        # Ensure plugdev membership
        usermod -aG plugdev "${SERVICE_USER}" 2>/dev/null || true
    fi
}

# --------------------------------------------------------------------------
# Directory Structure
# --------------------------------------------------------------------------

create_directories() {
    info "Creating directories..."
    mkdir -p "${INSTALL_DIR}" "${LOG_DIR}" "${SCAN_DIR}" "${CONFIG_DIR}"
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${LOG_DIR}"
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"
    ok "Directories created."
}

# --------------------------------------------------------------------------
# Application Install
# --------------------------------------------------------------------------

install_app() {
    info "Setting up Python virtual environment..."

    # Create venv as service user would use it, but owned by root for immutability
    python3 -m venv "${VENV_DIR}"

    info "Installing rf-monitor into venv..."
    "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel > /dev/null 2>&1
    "${VENV_DIR}/bin/pip" install "${REPO_DIR}" > /dev/null 2>&1

    # Verify
    if "${VENV_DIR}/bin/rf-monitor" --version > /dev/null 2>&1; then
        local version
        version=$("${VENV_DIR}/bin/rf-monitor" --version 2>&1)
        ok "rf-monitor installed: ${version}"
    else
        err "Installation verification failed."
        exit 1
    fi
}

# --------------------------------------------------------------------------
# udev Rules
# --------------------------------------------------------------------------

install_udev_rules() {
    local rules_src="${REPO_DIR}/deploy/99-rtlsdr.rules"
    local rules_dst="/etc/udev/rules.d/99-rtlsdr.rules"

    if [[ -f "${rules_src}" ]]; then
        info "Installing udev rules for RTL-SDR..."
        cp "${rules_src}" "${rules_dst}"
        udevadm control --reload-rules
        udevadm trigger
        ok "udev rules installed. Reconnect RTL-SDR if already plugged in."
    else
        warn "udev rules source not found at ${rules_src}, skipping."
    fi
}

# --------------------------------------------------------------------------
# Logrotate
# --------------------------------------------------------------------------

install_logrotate() {
    local src="${REPO_DIR}/deploy/rf-monitor.logrotate"
    local dst="/etc/logrotate.d/rf-monitor"

    if [[ -f "${src}" ]]; then
        info "Installing logrotate configuration..."
        cp "${src}" "${dst}"
        ok "Logrotate config installed."
    else
        warn "Logrotate config not found at ${src}, skipping."
    fi
}

# --------------------------------------------------------------------------
# Environment Config
# --------------------------------------------------------------------------

install_env_config() {
    local env_dst="${CONFIG_DIR}/env"
    local env_src="${REPO_DIR}/deploy/env.example"

    if [[ ! -f "${env_dst}" ]]; then
        if [[ -f "${env_src}" ]]; then
            info "Installing default environment config..."
            cp "${env_src}" "${env_dst}"
            ok "Environment config installed at ${env_dst}. Edit to customize."
        fi
    else
        ok "Environment config already exists at ${env_dst}, not overwriting."
    fi
}

# --------------------------------------------------------------------------
# systemd Service
# --------------------------------------------------------------------------

install_service() {
    local service_src="${REPO_DIR}/deploy/rf-monitor.service"
    local service_dst="/etc/systemd/system/rf-monitor.service"

    if [[ -f "${service_src}" ]]; then
        info "Installing systemd service..."
        cp "${service_src}" "${service_dst}"
        systemctl daemon-reload
        ok "systemd service installed."

        info "Enabling rf-monitor service (starts on boot)..."
        systemctl enable rf-monitor.service
        ok "Service enabled."

        echo ""
        info "To start now:       sudo systemctl start rf-monitor"
        info "To check status:    sudo systemctl status rf-monitor"
        info "To view logs:       sudo journalctl -u rf-monitor -f"
    else
        warn "Service file not found at ${service_src}, skipping."
    fi
}

# --------------------------------------------------------------------------
# Uninstall
# --------------------------------------------------------------------------

uninstall() {
    require_root
    info "Uninstalling rf-monitor..."

    # Stop and disable service
    systemctl stop rf-monitor.service 2>/dev/null || true
    systemctl disable rf-monitor.service 2>/dev/null || true
    rm -f /etc/systemd/system/rf-monitor.service
    systemctl daemon-reload

    # Remove app
    rm -rf "${INSTALL_DIR}"

    # Remove logrotate
    rm -f /etc/logrotate.d/rf-monitor

    # Keep logs and config (warn user)
    warn "Logs at ${LOG_DIR} and config at ${CONFIG_DIR} were NOT removed."
    warn "Remove manually if desired: sudo rm -rf ${LOG_DIR} ${CONFIG_DIR}"

    # Keep udev rules (other software may use them)
    warn "udev rules at /etc/udev/rules.d/99-rtlsdr.rules were NOT removed."

    # Don't remove user (may own files)
    warn "Service user '${SERVICE_USER}' was NOT removed."

    ok "rf-monitor uninstalled."
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

main() {
    local mode="${1:-full}"

    case "${mode}" in
        --uninstall)
            uninstall
            exit 0
            ;;
        --app)
            require_root
            install_app
            exit 0
            ;;
        full|*)
            require_root
            echo "============================================"
            echo "  rf-monitor — Raspberry Pi Installation"
            echo "============================================"
            echo ""

            install_system_deps
            blacklist_dvb_driver
            create_service_user
            create_directories
            install_app
            install_udev_rules
            install_logrotate
            install_env_config
            install_service

            echo ""
            echo "============================================"
            ok "Installation complete!"
            echo "============================================"
            echo ""
            info "Next steps:"
            info "  1. Edit /etc/rf-monitor/env to set your frequency range and thresholds"
            info "  2. Plug in your RTL-SDR USB dongle"
            info "  3. Test manually:  sudo -u rf-monitor ${VENV_DIR}/bin/rf-monitor scan -v"
            info "  4. Start service:  sudo systemctl start rf-monitor"
            info "  5. Monitor logs:   sudo journalctl -u rf-monitor -f"
            echo ""
            ;;
    esac
}

main "$@"
