#!/bin/bash
# Quick Start Script for Ubuntu

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     Internet Speed Monitor - Ubuntu Quick Start               ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${CYAN}📁 Directory: $SCRIPT_DIR${NC}"
echo ""

# Step 1: Check if setup has been run
echo -e "${CYAN}Step 1: Checking installation...${NC}"

if command -v speedtest &> /dev/null; then
    echo -e "${GREEN}✓${NC} speedtest installed"
elif command -v speedtest-cli &> /dev/null; then
    echo -e "${YELLOW}⚠${NC} speedtest-cli detected (works, but official speedtest is recommended)"
else
    echo -e "${YELLOW}⚠  speedtest engine not found${NC}"
    echo "   Run: ./setup.sh"
    exit 1
fi

if ! python3 -c "import pandas, matplotlib, cryptography" 2>/dev/null; then
    echo -e "${YELLOW}⚠  Python packages missing${NC}"
    echo "   Run: pip3 install -r requirements.txt"
    exit 1
else
    echo -e "${GREEN}✓${NC} Python packages installed"
fi

# Step 2: Check credentials
echo ""
echo -e "${CYAN}Step 2: Checking credentials...${NC}"

if [ ! -f "$SCRIPT_DIR/credentials.enc" ] || [ ! -f "$SCRIPT_DIR/.encryption_key" ]; then
    echo -e "${YELLOW}⚠  Credentials not configured${NC}"
    echo ""
    read -p "   Configure now? (yes/no): " -r
    if [[ $REPLY =~ ^[Yy]es$ ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 "$SCRIPT_DIR/update_credentials.py"
    else
        echo "   Run later: python3 update_credentials.py"
        exit 1
    fi
else
    echo -e "${GREEN}✓${NC} Credentials configured"
fi

# Step 3: Test speed test
echo ""
echo -e "${CYAN}Step 3: Running test speed test...${NC}"
echo ""

python3 "$SCRIPT_DIR/CheckSpeed.py"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓${NC} Speed test completed successfully!"
else
    echo ""
    echo -e "${RED}✗${NC} Speed test failed. Check errors.log"
    exit 1
fi

# Step 4: Offer to set up cron
echo ""
echo -e "${CYAN}Step 4: Set up automated scheduling?${NC}"
echo ""
read -p "   Configure cron jobs now? (yes/no): " -r

if [[ $REPLY =~ ^[Yy]es$ ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Add these lines to your crontab:"
    echo ""
    echo -e "${CYAN}# Run speed test 3 times daily (8 AM, 4 PM, 10 PM)${NC}"
    echo "0 8,16,22 * * * cd $SCRIPT_DIR && /usr/bin/python3 CheckSpeed.py >> cron.log 2>&1"
    echo ""
    echo -e "${CYAN}# Send weekly report every Monday at 8 AM${NC}"
    echo "0 8 * * 1 cd $SCRIPT_DIR && /usr/bin/python3 SendWeeklyReport.py >> cron.log 2>&1"
    echo ""
    read -p "Press Enter to open crontab editor..."
    crontab -e
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Setup Complete! 🎉                         ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "📚 Documentation:"
echo "   • Full guide: cat UBUNTU_SETUP.md"
echo "   • Migration info: cat MIGRATION_SUMMARY.md"
echo ""
echo "🔧 Useful commands:"
echo "   • Run speed test: python3 CheckSpeed.py"
echo "   • Weekly report: python3 SendWeeklyReport.py"
echo "   • Update credentials: python3 update_credentials.py"
echo "   • View cron jobs: crontab -l"
echo "   • View logs: tail -f cron.log"
echo ""
echo "Happy monitoring! 🚀"
echo ""
