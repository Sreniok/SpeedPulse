#!/bin/bash
# =====================================================================================
# SpeedPulse - Ubuntu Setup Script
# =====================================================================================
# This script will install all dependencies and configure the SpeedPulse system
# =====================================================================================

set -e  # Exit on error

echo ""
echo "=== SpeedPulse - Ubuntu Setup ==="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}📁 Installation directory: $SCRIPT_DIR${NC}"
echo ""

# Check if running as root (allow in containers/LXC)
IS_ROOT=0
if [ "$EUID" -eq 0 ]; then
    # Check if we're in a container environment
    if [ -f /.dockerenv ] || [ -f /run/systemd/container ] || grep -qa container=lxc /proc/1/environ 2>/dev/null; then
        echo -e "${YELLOW}⚠️  Running as root in container/LXC environment - proceeding${NC}"
        echo ""
        IS_ROOT=1
    else
        echo -e "${RED}❌ Please do not run this script as root/sudo${NC}"
        echo "   Run without sudo. You'll be prompted for password when needed."
        exit 1
    fi
fi

# Helper function to run commands with or without sudo
run_cmd() {
    if [ $IS_ROOT -eq 1 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

# ── STEP 1: Update system ────────────────────────────────────────────
echo -e "${CYAN}[1/6] Updating package list...${NC}"
run_cmd apt update

# ── STEP 2: Install Python 3 and pip ─────────────────────────────────
echo ""
echo -e "${CYAN}[2/6] Checking Python 3...${NC}"
if ! command -v python3 &> /dev/null; then
    echo "   Installing Python 3..."
    run_cmd apt install -y python3 python3-pip python3-venv
else
    echo -e "   ${GREEN}✓${NC} Python 3 is already installed: $(python3 --version)"
    # Ensure pip is installed even if Python exists
    if ! command -v pip3 &> /dev/null; then
        echo "   Installing pip3..."
        run_cmd apt install -y python3-pip
    fi
fi

# ── STEP 3: Install speedtest engine ─────────────────────────────────
echo ""
echo -e "${CYAN}[3/6] Checking speedtest engine...${NC}"

if ! command -v speedtest &> /dev/null; then
    echo "   Installing official Ookla speedtest CLI..."
    run_cmd apt install -y ca-certificates curl gnupg
    run_cmd mkdir -p /etc/apt/keyrings
    curl -fsSL https://packagecloud.io/ookla/speedtest-cli/gpgkey \
        | run_cmd gpg --dearmor --yes -o /etc/apt/keyrings/ookla-speedtest.gpg

    DISTRO_ID="$(. /etc/os-release && echo "${ID}")"
    DISTRO_CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    if [ -z "$DISTRO_CODENAME" ]; then
        DISTRO_CODENAME="bookworm"
    fi

    REPO_FLAVOR="debian"
    if [ "$DISTRO_ID" = "ubuntu" ]; then
        REPO_FLAVOR="ubuntu"
    fi

    echo "deb [signed-by=/etc/apt/keyrings/ookla-speedtest.gpg] https://packagecloud.io/ookla/speedtest-cli/${REPO_FLAVOR}/ ${DISTRO_CODENAME} main" \
        | run_cmd tee /etc/apt/sources.list.d/ookla-speedtest.list >/dev/null

    run_cmd apt update
    run_cmd apt install -y speedtest
else
    echo -e "   ${GREEN}✓${NC} speedtest is already installed"
fi

# Test speedtest
echo "   Testing speedtest..."
if speedtest --version &> /dev/null; then
    echo -e "   ${GREEN}✓${NC} speedtest is working"
else
    echo -e "   ${YELLOW}⚠${NC}  speedtest may not be working correctly"
fi

# ── STEP 4: Install Python dependencies ──────────────────────────────
echo ""
echo -e "${CYAN}[4/6] Installing Python packages...${NC}"

# Create a requirements list
cat > /tmp/speedtest_requirements.txt << EOF
pandas>=2.0.0
matplotlib>=3.7.0
cryptography>=41.0.0
EOF

echo "   Installing: pandas, matplotlib, cryptography"
if [ $IS_ROOT -eq 1 ]; then
    pip3 install --break-system-packages -r /tmp/speedtest_requirements.txt
else
    pip3 install --user -r /tmp/speedtest_requirements.txt
fi
rm /tmp/speedtest_requirements.txt

echo -e "   ${GREEN}✓${NC} Python packages installed"

# ── STEP 5: Create necessary directories ─────────────────────────────
echo ""
echo -e "${CYAN}[5/6] Creating directories...${NC}"

mkdir -p "$SCRIPT_DIR/Log"
mkdir -p "$SCRIPT_DIR/Images"

echo -e "   ${GREEN}✓${NC} Directories created"

# ── STEP 6: Make scripts executable ──────────────────────────────────
echo ""
echo -e "${CYAN}[6/6] Making scripts executable...${NC}"

chmod +x "$SCRIPT_DIR/CheckSpeed.py"
chmod +x "$SCRIPT_DIR/SendAlert.py"
chmod +x "$SCRIPT_DIR/SendWeeklyReport.py"
chmod +x "$SCRIPT_DIR/SpeedChart.py"
chmod +x "$SCRIPT_DIR/update_credentials.py"
chmod +x "$SCRIPT_DIR/credentials_manager.py"
chmod +x "$SCRIPT_DIR/setup.sh"

echo -e "   ${GREEN}✓${NC} Scripts are executable"

# ── UPDATE CONFIG.JSON ───────────────────────────────────────────────
echo ""
echo -e "${CYAN}Updating config.json for Linux paths...${NC}"

# Backup original config
if [ -f "$SCRIPT_DIR/config.json" ]; then
    cp "$SCRIPT_DIR/config.json" "$SCRIPT_DIR/config.json.bak"
    echo -e "   ${GREEN}✓${NC} Backed up config.json to config.json.bak"
fi

# Update paths in config.json using Python
python3 << EOF
import json
from pathlib import Path

config_file = Path("$SCRIPT_DIR/config.json")
if config_file.exists():
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    # Update paths for Linux
    config['paths']['speedtest_exe'] = 'speedtest'
    config['paths']['log_directory'] = '$SCRIPT_DIR/Log'
    config['paths']['images_directory'] = '$SCRIPT_DIR/Images'
    config['paths']['database_file'] = '$SCRIPT_DIR/speedtest.db'
    config['paths']['chart_base64'] = '$SCRIPT_DIR/chart_base64.txt'
    config['paths']['error_log'] = '$SCRIPT_DIR/errors.log'
    
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print("   Config paths updated for Linux")
EOF

echo -e "   ${GREEN}✓${NC} config.json updated"

# ── SETUP CREDENTIALS ────────────────────────────────────────────────
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Installation completed successfully!${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}📝 Next steps:${NC}"
echo ""
echo "1. Configure email credentials:"
echo -e "   ${CYAN}python3 update_credentials.py${NC}"
echo ""
echo "2. Test speed test:"
echo -e "   ${CYAN}python3 CheckSpeed.py${NC}"
echo ""
echo "3. Set up automated scheduling (cron):"
echo -e "   ${CYAN}crontab -e${NC}"
echo ""
echo "   Add these lines:"
echo -e "   ${CYAN}# Run speed test 3 times daily${NC}"
echo -e "   ${CYAN}0 8,16,22 * * * cd $SCRIPT_DIR && python3 CheckSpeed.py >> cron.log 2>&1${NC}"
echo ""
echo -e "   ${CYAN}# Send weekly report every Monday at 8 AM${NC}"
echo -e "   ${CYAN}0 8 * * 1 cd $SCRIPT_DIR && python3 SendWeeklyReport.py >> cron.log 2>&1${NC}"
echo ""
echo "4. Check the README.md for more information"
echo ""
echo -e "${GREEN}Happy monitoring! 🚀${NC}"
echo ""
