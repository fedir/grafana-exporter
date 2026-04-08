#!/usr/bin/env bash
# setup.sh — Install dependencies for grafana_export.py
# Supports: Linux (Ubuntu, Debian, Mint, Fedora, RHEL, Arch, …), macOS
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

OS="$(uname -s)"

# ── Python 3.9+ ───────────────────────────────────────────────────────────────
check_python() {
  if command -v python3 &>/dev/null; then
    info "Found $(python3 --version)"
  else
    error "python3 not found. Install it first:"
    if [[ "$OS" == "Darwin" ]]; then
      echo "  brew install python"
    elif command -v apt-get &>/dev/null; then
      echo "  sudo apt-get install python3 python3-venv"
    elif command -v dnf &>/dev/null; then
      echo "  sudo dnf install python3"
    else
      echo "  Install python3 with your distribution's package manager."
    fi
    exit 1
  fi
}

# ── pip / venv ────────────────────────────────────────────────────────────────
setup_venv() {
  VENV_DIR="$(dirname "$0")/.venv"
  if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment at $VENV_DIR …"
    python3 -m venv "$VENV_DIR"
  else
    info "Virtual environment already exists: $VENV_DIR"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip --quiet
}

# ── Playwright ────────────────────────────────────────────────────────────────
install_playwright() {
  info "Installing Playwright Python package …"
  pip install playwright --quiet

  if [[ "$OS" == "Linux" ]]; then
    info "Installing system libraries required by Playwright's Chromium …"
    # playwright install-deps detects the current distro and installs the right
    # packages automatically (apt, dnf, pacman, etc.).  Works on Ubuntu, Debian,
    # Linux Mint, Fedora, RHEL/CentOS, Arch, and others.
    if python3 -m playwright install-deps chromium; then
      info "System dependencies installed."
    else
      warn "playwright install-deps failed or requires sudo."
      warn "Re-run with: sudo python3 -m playwright install-deps chromium"
      warn "Then run this script again."
      exit 1
    fi
  fi

  info "Downloading Chromium browser for Playwright …"
  python3 -m playwright install chromium

  info "Playwright + Chromium ready."
}

# ── Main ──────────────────────────────────────────────────────────────────────
check_python
setup_venv
install_playwright

echo ""
info "Setup complete!"
echo ""
echo "Activate the environment and run the exporter:"
echo ""
echo "  source .venv/bin/activate"
echo ""
echo "  # Export a specific dashboard:"
echo "  python3 grafana_export.py \\"
echo "    --user admin --password secret \\"
echo "    'http://localhost:3000/d/<uid>/<slug>?from=now-1h&to=now'"
echo ""
echo "  # Export ALL dashboards:"
echo "  python3 grafana_export.py --all --user admin --password secret"
echo ""
echo "  # Use an API token:"
echo "  python3 grafana_export.py --token glsa_YOUR_TOKEN \\"
echo "    'http://localhost:3000/d/<uid>/<slug>?from=now-1h&to=now'"
echo ""
echo "Output goes to ./grafana-export/ as self-contained .html files."
