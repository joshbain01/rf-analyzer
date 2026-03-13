#!/usr/bin/env bash
# deploy.sh — Push rf-monitor from dev machine to Raspberry Pi via rsync
#
# Usage:
#   ./deploy.sh pi@192.168.1.100              # Deploy + install
#   ./deploy.sh pi@raspberrypi.local           # mDNS hostname
#   ./deploy.sh pi@192.168.1.100 --sync-only   # Sync files only, no install
#
# Prerequisites:
#   - SSH key auth configured to the Pi (ssh-copy-id pi@<host>)
#   - rsync installed on both machines

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"
REMOTE_DIR="/opt/rf-monitor/src"

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
err()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

usage() {
    echo "Usage: $0 <user@host> [--sync-only]"
    echo ""
    echo "Arguments:"
    echo "  user@host     SSH target (e.g., pi@192.168.1.100)"
    echo "  --sync-only   Only sync files, skip remote install"
    echo ""
    echo "Examples:"
    echo "  $0 pi@192.168.1.100"
    echo "  $0 pi@raspberrypi.local --sync-only"
    exit 1
}

# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
    usage
fi

TARGET="$1"
SYNC_ONLY="${2:-}"

# Validate target format
if [[ ! "${TARGET}" =~ .+@.+ ]]; then
    err "Invalid target '${TARGET}'. Expected format: user@host"
    usage
fi

# Check rsync
if ! command -v rsync &>/dev/null; then
    err "rsync is required but not installed."
    err "  Windows: Install via Git for Windows or WSL"
    err "  macOS:   brew install rsync"
    err "  Linux:   sudo apt install rsync"
    exit 1
fi

# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------

info "Syncing rf-monitor to ${TARGET}:${REMOTE_DIR}..."

ssh "${TARGET}" "sudo mkdir -p ${REMOTE_DIR} && sudo chown \$(whoami) ${REMOTE_DIR}"

rsync -avz --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache' \
    --exclude '*.egg-info' \
    --exclude '.git' \
    --exclude 'analysis/' \
    --exclude 'logs/' \
    --exclude 'venv/' \
    --exclude '.venv/' \
    "${REPO_DIR}/" "${TARGET}:${REMOTE_DIR}/"

ok "Files synced to ${TARGET}:${REMOTE_DIR}"

# --------------------------------------------------------------------------
# Remote Install
# --------------------------------------------------------------------------

if [[ "${SYNC_ONLY}" == "--sync-only" ]]; then
    info "Sync-only mode. Skipping remote install."
    info "Run install manually:  ssh ${TARGET} 'sudo bash ${REMOTE_DIR}/deploy/install.sh'"
    exit 0
fi

info "Running remote install..."
ssh -t "${TARGET}" "sudo bash ${REMOTE_DIR}/deploy/install.sh"

echo ""
ok "Deployment complete!"
info "Service management:"
info "  Start:   ssh ${TARGET} 'sudo systemctl start rf-monitor'"
info "  Stop:    ssh ${TARGET} 'sudo systemctl stop rf-monitor'"
info "  Status:  ssh ${TARGET} 'sudo systemctl status rf-monitor'"
info "  Logs:    ssh ${TARGET} 'sudo journalctl -u rf-monitor -f'"
