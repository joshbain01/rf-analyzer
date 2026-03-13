#!/usr/bin/env bash
# deploy.sh — Push rf-monitor from dev machine to Raspberry Pi via rsync
#
# Usage:
#   ./deploy.sh pi@<pi-host>                  # Deploy + install
#   ./deploy.sh pi@raspberrypi.local           # mDNS hostname
#   ./deploy.sh pi@<pi-host> --sync-only       # Sync files only, no install
#   ./deploy.sh pi@<pi-host> --profile wideband
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
    local code="${1:-1}"
    echo "Usage: $0 <user@host> [--sync-only] [--profile <name>]"
    echo ""
    echo "Arguments:"
    echo "  user@host     SSH target (e.g., pi@<pi-host>)"
    echo "  --sync-only   Only sync files, skip remote install"
    echo "  --profile     Deployment profile name passed to install.sh (default: balanced)"
    echo ""
    echo "Examples:"
    echo "  $0 pi@<pi-host>"
    echo "  $0 pi@raspberrypi.local --sync-only"
    echo "  $0 pi@<pi-host> --profile high-sensitivity"
    exit "${code}"
}

# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
    usage
fi

TARGET="$1"
SYNC_ONLY="false"
PROFILE="balanced"

shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --sync-only)
            SYNC_ONLY="true"
            shift
            ;;
        --profile)
            if [[ $# -lt 2 ]]; then
                err "--profile requires a value"
                usage
            fi
            PROFILE="$2"
            shift 2
            ;;
        -h|--help)
            usage 0
            ;;
        *)
            err "Unknown argument: $1"
            usage
            ;;
    esac
done

if [[ ! "${PROFILE}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    err "Invalid profile '${PROFILE}'. Use letters, digits, dot, dash, or underscore."
    exit 1
fi

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

if [[ "${SYNC_ONLY}" == "true" ]]; then
    info "Sync-only mode. Skipping remote install."
    info "Run install manually:  ssh ${TARGET} 'sudo bash ${REMOTE_DIR}/deploy/install.sh --profile ${PROFILE}'"
    exit 0
fi

info "Running remote install with profile '${PROFILE}'..."
ssh -t "${TARGET}" "sudo bash ${REMOTE_DIR}/deploy/install.sh --profile ${PROFILE}"

echo ""
ok "Deployment complete!"
info "Service management:"
info "  Start:   ssh ${TARGET} 'sudo systemctl start rf-monitor'"
info "  Stop:    ssh ${TARGET} 'sudo systemctl stop rf-monitor'"
info "  Status:  ssh ${TARGET} 'sudo systemctl status rf-monitor'"
info "  Logs:    ssh ${TARGET} 'sudo journalctl -u rf-monitor -f'"
