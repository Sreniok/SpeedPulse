#!/bin/bash
# Cross-Platform Deployment Script
# Deploy Speedtest project from NAS to Ubuntu Server
# Works on: macOS, Linux (Ubuntu), Windows (Git Bash/WSL)

set -e  # Exit on error

# ============================================================================
# CONFIGURATION - Edit these variables for your setup
# ============================================================================

# Ubuntu server details (override via env vars or be prompted)
SERVER_USER="${DEPLOY_USER:-}"
SERVER_HOST="${DEPLOY_HOST:-}"
SERVER_PATH="${DEPLOY_PATH:-~/scripts/Speedtest}"

# Source path (current directory)
SOURCE_PATH="$(cd "$(dirname "$0")" && pwd)"

# Files/folders to exclude from sync
EXCLUDE_PATTERNS=(
    "__pycache__"
    "*.pyc"
    ".encryption_key"
    "credentials.enc"
    "*.log"
    "cron.log"
    "errors.log"
    "last_alert.txt"
    ".DS_Store"
    "*.bak"
    "config.json.bak"
    "Log/"
    "Images/"
)

# ============================================================================
# COLORS
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# FUNCTIONS
# ============================================================================

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}  Speedtest Deployment Tool${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

check_dependencies() {
    print_info "Checking dependencies..."
    
    if ! command -v rsync &> /dev/null; then
        print_error "rsync is not installed"
        echo ""
        echo "Install rsync:"
        echo "  macOS:   brew install rsync"
        echo "  Ubuntu:  sudo apt install rsync"
        echo "  Windows: Install Git Bash or WSL"
        exit 1
    fi
    
    if ! command -v ssh &> /dev/null; then
        print_error "ssh is not installed"
        exit 1
    fi
    
    print_success "Dependencies OK"
}

check_server_connection() {
    print_info "Testing connection to ${SERVER_USER}@${SERVER_HOST}..."
    
    # First try with SSH key (no password prompt)
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${SERVER_USER}@${SERVER_HOST}" "exit" 2>/dev/null; then
        print_success "Connection successful (using SSH key) ✓"
        SSH_KEY_AUTH=true
        return 0
    fi
    
    # SSH key not set up, will require password
    print_warning "SSH key not detected - you will be asked for password during deployment"
    echo ""
    echo "For password-less deployment, set up SSH key (optional):"
    echo "  ssh-keygen -t rsa -b 4096"
    echo "  ssh-copy-id ${SERVER_USER}@${SERVER_HOST}"
    echo ""
    
    SSH_KEY_AUTH=false
    
    # Try regular connection (will prompt for password)
    if ssh -o ConnectTimeout=5 "${SERVER_USER}@${SERVER_HOST}" "exit"; then
        print_success "Connection successful (using password)"
        return 0
    else
        print_error "Cannot connect to server"
        echo ""
        echo "Please ensure:"
        echo "  1. Server is reachable: ping ${SERVER_HOST}"
        echo "  2. SSH is configured: ssh ${SERVER_USER}@${SERVER_HOST}"
        echo "  3. SSH service is running on server"
        exit 1
    fi
}

show_config() {
    echo ""
    echo "Configuration:"
    echo "  Source:      ${SOURCE_PATH}"
    echo "  Destination: ${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}"
    echo "  Platform:    $(uname -s)"
    echo ""
}

build_exclude_args() {
    local exclude_args=""
    for pattern in "${EXCLUDE_PATTERNS[@]}"; do
        exclude_args="$exclude_args --exclude='$pattern'"
    done
    echo "$exclude_args"
}

deploy_files() {
    print_info "Creating destination directory on server..."
    ssh "${SERVER_USER}@${SERVER_HOST}" "mkdir -p ${SERVER_PATH}"
    
    print_info "Deploying files to Ubuntu server..."
    echo ""
    
    # Build exclude arguments
    local exclude_args=""
    for pattern in "${EXCLUDE_PATTERNS[@]}"; do
        exclude_args="$exclude_args --exclude=$pattern"
    done
    
    # Run rsync
    rsync -avz --delete \
        $exclude_args \
        --progress \
        "${SOURCE_PATH}/" \
        "${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}/"
    
    if [ $? -eq 0 ]; then
        print_success "Files deployed successfully"
    else
        print_error "Deployment failed"
        exit 1
    fi
}

set_permissions() {
    print_info "Setting file permissions on server..."
    
    ssh "${SERVER_USER}@${SERVER_HOST}" "cd ${SERVER_PATH} && chmod +x *.sh *.py" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        print_success "Permissions set"
    else
        print_warning "Could not set permissions (may need to do manually)"
    fi
}

show_next_steps() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Deployment Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "Files synchronized to: ${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}"
    echo ""
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    print_header
    
    # Prompt for missing server details
    if [ -z "$SERVER_USER" ]; then
        read -p "Server username: " SERVER_USER
        if [ -z "$SERVER_USER" ]; then
            print_error "SERVER_USER is required (set DEPLOY_USER env var or enter at prompt)"
            exit 1
        fi
    fi
    if [ -z "$SERVER_HOST" ]; then
        read -p "Server host/IP: " SERVER_HOST
        if [ -z "$SERVER_HOST" ]; then
            print_error "SERVER_HOST is required (set DEPLOY_HOST env var or enter at prompt)"
            exit 1
        fi
    fi
    
    check_dependencies
    show_config
    
    # Confirm deployment
    read -p "Deploy to ${SERVER_USER}@${SERVER_HOST}? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_warning "Deployment cancelled"
        exit 0
    fi
    
    check_server_connection
    deploy_files
    set_permissions
    show_next_steps
}

main "$@"
