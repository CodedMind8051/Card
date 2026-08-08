#!/usr/bin/env bash
#
# setup.sh — one-shot installer for the Student ID Card Automation tool.
#
# What it does
#   1. Installs the system libraries (apt-based: Debian/Ubuntu/Kali).
#   2. Creates a Python virtual environment under venv/.
#   3. Installs the Python dependencies from requirements.txt.
#   4. Installs the Playwright Firefox browser + its system deps.
#   5. Creates the runtime working folders.
#
# Usage
#   cd /student-id-card
#   bash setup.sh                # prompts for sudo for the apt step
#   bash setup.sh --skip-system-deps   # skip the apt install step
#
# Idempotent: safe to run more than once.

set -euo pipefail

C_GOOD='\033[1;32m'; C_WARN='\033[1;33m'; C_INFO='\033[1;36m'; C_DIM='\033[2m'; C_END='\033[0m'
note() { printf "${C_INFO}==>${C_END} %s\n" "$*"; }
ok()   { printf "${C_GOOD}[ ok ]${C_END} %s\n" "$*"; }
warn() { printf "${C_WARN}[ !! ]${C_END} %s\n" "$*"; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

# --------------------------------------------------------------------------
# 0. Sanity checks
# --------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found. Please install Python 3.11+ first and re-run."
  exit 1
fi
ok "Found python3: $(command -v python3) ($(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")'))"

SKIP_SYSTEM=0
if [ "${1:-}" = "--skip-system-deps" ]; then
  SKIP_SYSTEM=1
  warn "Skipping the system-packages step."
fi

# --------------------------------------------------------------------------
# 1. System packages (apt-based distros only)
# --------------------------------------------------------------------------
maybe_sudo() {
  [ "$(id -u)" = "0" ] && return 0
  command -v sudo >/dev/null 2>&1 && { printf "sudo "; return 0; }
  return 1
}

if [ "$SKIP_SYSTEM" = "0" ] && command -v apt-get >/dev/null 2>&1; then
  note "Installing system packages (apt-get)..."
  SUDO="$(maybe_sudo)" || { warn "Need root or sudo for apt; install manually or run with --skip-system-deps."; exit 1; }
  $SUDO apt-get update -y
  $SUDO apt-get install -y \
    python3-venv python3-pip \
    fonts-dejavu-core \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libxkbcommon0 libatspi2.0-0 libx11-xcb1 libxcursor1 \
    libxss1 libxdamage1 libgtk-3-0
  ok "System packages installed."
elif [ "$SKIP_SYSTEM" = "0" ]; then
  warn "apt-get not found. Please install the libraries for your distro manually."
fi

# --------------------------------------------------------------------------
# 2. Python virtualenv
# --------------------------------------------------------------------------
if [ ! -d "$here/venv" ]; then
  note "Creating virtual environment (venv/)..."
  python3 -m venv "$here/venv"
fi
# shellcheck disable=SC1091
source "$here/venv/bin/activate"
ok "Using $(command -v python)"

# --------------------------------------------------------------------------
# 3. Python dependencies
# --------------------------------------------------------------------------
note "Installing Python dependencies (requirements.txt)..."
python -m pip install --upgrade pip
python -m pip install -r "$here/requirements.txt"
ok "Python dependencies installed."

# --------------------------------------------------------------------------
# 4. Playwright Firefox
# --------------------------------------------------------------------------
note "Installing Playwright Firefox browser + dependencies..."
python -m playwright install firefox
python -m playwright install-deps firefox || true
ok "Playwright Firefox installed."

# --------------------------------------------------------------------------
# 5. Workspace folders
# --------------------------------------------------------------------------
note "Creating workspace folders..."
mkdir -p "$here"/{image,output,retry,temp,templates,history,completed}
mkdir -p "$here/output/cards" "$here/output/records"
ok "Folders created."

# --------------------------------------------------------------------------
# 6. Wrap up
# --------------------------------------------------------------------------
cat <<EOF

${C_GOOD}Setup complete.${C_END}

Next steps
  1) Put scanned admission forms into:  ${C_DIM}image/${C_END}
  2) Design your card template:         ${C_DIM}python main/cardfiller.py --new${C_END}
     (browser opens: layout photo + fields, set a 4-colour gradient, click
     Save. This becomes the active template for every run.)
  3) Run the batch fill:                ${C_DIM}python main/cardfiller.py${C_END}
     add ${C_DIM}--image-enhance${C_END} to enhance each student photo.

Archive / resume batches
   ${C_DIM}python main/cardfiller.py --mark-old batch1${C_END}   # zip batch -> history/
   ${C_DIM}python main/cardfiller.py --given-name batch1${C_END}  # restore + resume

Make an A4 PDF of the cards:
   ${C_DIM}python pdf_maker.py [--enhance-image]${C_END}

One more thing: the scraper uses a persistent Firefox profile. See the
README "Firefox profile / Google login" section — you must be logged into
Google in that profile before scraping.

Activate the environment later with:  ${C_DIM}source venv/bin/activate${C_END}
EOF