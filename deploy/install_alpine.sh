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

echo "🔧 Step 5: Installing OpenRC background service..."
cp "$INSTALL_DIR/deploy/solaredge-tesla.initd" "/etc/init.d/$SERVICE_NAME"
chmod +x "/etc/init.d/$SERVICE_NAME"

echo ""
echo "=========================================================="
echo " Installation Complete!"
echo "=========================================================="
echo ""
echo "Next steps:"
echo " 1. Edit your configuration file:"
echo "    nano $INSTALL_DIR/config.yaml"
echo ""
echo " 2. Authorize your Tesla account:"
echo "    cd $INSTALL_DIR"
echo "    $INSTALL_DIR/venv/bin/python authenticate_tesla.py"
echo ""
echo " 3. Test your SolarEdge Modbus connection:"
echo "    $INSTALL_DIR/venv/bin/python test_modbus.py"
echo ""
echo " 4. Enable and start the background service:"
echo "    rc-update add $SERVICE_NAME default"
echo "    rc-service $SERVICE_NAME start"
echo ""
echo " Check service logs:"
echo "    tail -f /var/log/solaredge-tesla.log"
echo "=========================================================="
