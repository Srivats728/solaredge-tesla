#!/bin/sh
#
# Automated Installer for Alpine Linux Proxmox LXC
# Sets up SolarEdge-Tesla Optimizer as a background OpenRC service
#

set -e

INSTALL_DIR="/opt/solaredge-tesla"
SERVICE_NAME="solaredge-tesla"

echo "=========================================================="
echo " SolarEdge - Tesla Optimizer: Alpine LXC Setup"
echo "=========================================================="

if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Error: This script must be run as root inside the Alpine LXC."
    exit 1
fi

echo "📦 Step 1: Updating package repository and installing dependencies..."
apk update
apk add --no-cache python3 py3-pip py3-virtualenv git gcc musl-dev libffi-dev openssl-dev tzdata ca-certificates

echo "📂 Step 2: Preparing directory structure at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Copy files from script's parent directory if running from cloned repository
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    echo "Copying files from $SCRIPT_DIR to $INSTALL_DIR..."
    cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR"/
fi

cd "$INSTALL_DIR"

echo "🐍 Step 3: Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "⚙️ Step 4: Setting up configuration..."
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$INSTALL_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    echo "Created $INSTALL_DIR/config.yaml from example."
fi

echo "🔧 Step 5: Installing and starting OpenRC background service..."
cp "$INSTALL_DIR/deploy/solaredge-tesla.initd" "/etc/init.d/$SERVICE_NAME"
chmod +x "/etc/init.d/$SERVICE_NAME"
rc-update add "$SERVICE_NAME" default || true
rc-service "$SERVICE_NAME" restart || rc-service "$SERVICE_NAME" start || true

MY_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1 || hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo ""
echo "=========================================================="
echo " 🎉 Installation Complete & Web Dashboard Started!"
echo "=========================================================="
echo ""
echo "Open the Web Dashboard in your browser:"
echo "   👉 http://${MY_IP}:8080"
echo ""
echo "From the web dashboard you can:"
echo " - View live SolarEdge generation & Tesla charging status"
echo " - Complete Tesla login with 1 click"
echo " - Test your Modbus connection"
echo " - Fine-tune threshold watts and settings anytime"
echo "=========================================================="

