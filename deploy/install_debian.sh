#!/usr/bin/env bash
#
# Automated Installer for Debian (12 / 13) Proxmox LXC
# Sets up SolarEdge-Tesla Optimizer & Web Dashboard as a systemd service
#

set -e

INSTALL_DIR="/opt/solaredge-tesla"
SERVICE_NAME="solaredge-tesla"

# Color definitions
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}===================================================================${NC}"
echo -e "${CYAN}   ☀️🚗 SolarEdge ↔ Tesla Optimizer: Debian LXC Setup            ${NC}"
echo -e "${CYAN}===================================================================${NC}"

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}❌ Error: This script must be run as root.${NC}"
    exit 1
fi

echo -e "${BLUE}📦 Step 1: Installing required system packages via APT...${NC}"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    gcc \
    libffi-dev \
    libssl-dev \
    tzdata \
    ca-certificates

echo -e "${BLUE}📂 Step 2: Preparing directory structure at ${INSTALL_DIR}...${NC}"
mkdir -p "$INSTALL_DIR"

# Copy files from script's parent directory if running from cloned repository
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    echo "Copying files from $SCRIPT_DIR to $INSTALL_DIR..."
    cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR"/
fi

cd "$INSTALL_DIR"

echo -e "${BLUE}🐍 Step 3: Setting up Python virtual environment...${NC}"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo -e "${BLUE}⚙️ Step 4: Initializing configuration...${NC}"
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$INSTALL_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    echo "Created $INSTALL_DIR/config.yaml from template."
fi

echo -e "${BLUE}🔧 Step 5: Installing systemd background service...${NC}"
cp "$INSTALL_DIR/deploy/solaredge-tesla.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

MY_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1 || echo "localhost")

echo ""
echo -e "${GREEN}===================================================================${NC}"
echo -e "${GREEN}   🎉 Installation Complete & Web Dashboard is Running!           ${NC}"
echo -e "${GREEN}===================================================================${NC}"
echo ""
echo -e "Open the ${CYAN}Web Dashboard${NC} in your browser to complete setup:"
echo ""
echo -e "   👉 ${GREEN}http://${MY_IP}:8080${NC}"
echo ""
echo -e "From the web dashboard you can:"
echo -e " • View live SolarEdge generation & Tesla charging status"
echo -e " • Complete Tesla login with 1 click (no CLI required)"
echo -e " • Test your Modbus connection"
echo -e " • Fine-tune threshold watts & settings anytime"
echo -e "${GREEN}===================================================================${NC}"
