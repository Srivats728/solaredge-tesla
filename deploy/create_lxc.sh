#!/usr/bin/env bash
#
# Proxmox VE 1-Step Turnkey Deployment Script
# Automatically creates an Alpine LXC, installs the SolarEdge-Tesla Optimizer,
# and starts the Web Dashboard on port 8080.
#

set -e

# Color definitions
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}===================================================================${NC}"
echo -e "${CYAN}   SolarEdge / Tesla Optimizer: Proxmox Turnkey Installer        ${NC}"
echo -e "${CYAN}===================================================================${NC}"

# Check if running inside an existing container or on Proxmox VE Host
if [ ! -d "/etc/pve" ]; then
    echo -e "${YELLOW}Notice: /etc/pve not detected.${NC}"
    echo -e "It looks like you are running this inside an existing Linux container/VM."
    echo -e "Executing container-level installer directly..."
    
    if command -v apk >/dev/null 2>&1; then
        apk add --no-cache git curl
        if [ ! -d "/opt/solaredge-tesla" ]; then
            git clone https://github.com/Srivats728/solaredge-tesla.git /opt/solaredge-tesla
        fi
        cd /opt/solaredge-tesla
        sh deploy/install_alpine.sh
        rc-service solaredge-tesla start || true
        
        MY_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1 || hostname -I | awk '{print $1}')
        echo ""
        echo -e "${GREEN}Installation Complete!${NC}"
        echo -e "${GREEN}Open the Web Dashboard in your browser:${NC}"
        echo -e "${CYAN}URL: http://${MY_IP}:8080${NC}"
        exit 0
    else
        echo -e "${RED}Error: Neither Proxmox host nor Alpine Linux environment detected.${NC}"
        exit 1
    fi
fi

# We are on Proxmox VE Node Host
echo -e "${BLUE}Detected Proxmox VE Node Host.${NC}"

# 1. Determine Container ID
NEXT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo "150")
read -p "Enter Container ID to use [$NEXT_ID]: " CT_ID
CT_ID=${CT_ID:-$NEXT_ID}

# 2. Storage Selection
STORAGE="local"
if pvesm status 2>/dev/null | grep -q "local-lvm"; then
    DISK_STORAGE="local-lvm"
elif pvesm status 2>/dev/null | grep -q "local-zfs"; then
    DISK_STORAGE="local-zfs"
else
    DISK_STORAGE="local"
fi

# 3. Update Appliance Templates and find Alpine
echo -e "${BLUE}Updating Proxmox appliance template index...${NC}"
pveam update >/dev/null 2>&1 || true

ALPINE_TEMPLATE=$(pveam available --section system 2>/dev/null | grep "alpine" | tail -n 1 | awk '{print $2}')
if [ -z "$ALPINE_TEMPLATE" ]; then
    ALPINE_TEMPLATE="alpine-3.20-default_20240606_amd64.tar.xz"
fi

# Check if template is already downloaded
TEMPLATE_EXISTS=$(pveam list "$STORAGE" 2>/dev/null | grep "alpine" | tail -n 1 | awk '{print $1}' || true)
if [ -z "$TEMPLATE_EXISTS" ]; then
    echo -e "${YELLOW}Downloading Alpine Linux template: ${ALPINE_TEMPLATE}...${NC}"
    pveam download "$STORAGE" "$ALPINE_TEMPLATE"
    TEMPLATE_PATH="${STORAGE}:vztmpl/${ALPINE_TEMPLATE}"
else
    TEMPLATE_PATH="$TEMPLATE_EXISTS"
    echo -e "${GREEN}Using existing template: ${TEMPLATE_PATH}${NC}"
fi

# 4. Create the Container
echo -e "${BLUE}Creating LXC Container ${CT_ID} (hostname: solaredge-tesla)...${NC}"
pct create "$CT_ID" "$TEMPLATE_PATH" \
    --ostype alpine \
    --hostname solaredge-tesla \
    --cores 1 \
    --memory 512 \
    --swap 256 \
    --features nesting=1 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp,firewall=0 \
    --storage "$DISK_STORAGE" \
    --rootfs "${DISK_STORAGE}:2" \
    --unprivileged 1 \
    --onboot 1

# 5. Start Container
echo -e "${BLUE}Starting container ${CT_ID}...${NC}"
pct start "$CT_ID"

echo -e "${BLUE}Waiting for network lease...${NC}"
sleep 5

# 6. Install Dependencies & SolarEdge-Tesla inside container
echo -e "${BLUE}Installing dependencies and SolarEdge-Tesla inside LXC...${NC}"
pct exec "$CT_ID" -- apk update
pct exec "$CT_ID" -- apk add --no-cache git curl python3 py3-pip py3-virtualenv gcc musl-dev libffi-dev openssl-dev tzdata

pct exec "$CT_ID" -- rm -rf /opt/solaredge-tesla
pct exec "$CT_ID" -- git clone https://github.com/Srivats728/solaredge-tesla.git /opt/solaredge-tesla
pct exec "$CT_ID" -- sh /opt/solaredge-tesla/deploy/install_alpine.sh

# Start the OpenRC service
pct exec "$CT_ID" -- rc-update add solaredge-tesla default
pct exec "$CT_ID" -- rc-service solaredge-tesla restart

# 7. Get IP Address
CT_IP=""
for i in $(seq 1 10); do
    CT_IP=$(pct exec "$CT_ID" -- ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1 || true)
    if [ -n "$CT_IP" ]; then
        break
    fi
    sleep 1
done

echo ""
echo -e "${GREEN}===================================================================${NC}"
echo -e "${GREEN}   Installation Complete! Container ID: ${CT_ID}                  ${NC}"
echo -e "${GREEN}===================================================================${NC}"
echo ""
if [ -n "$CT_IP" ]; then
    echo -e "Open the ${CYAN}Web Dashboard${NC} in your browser to complete setup:"
    echo ""
    echo -e "   URL: ${GREEN}http://${CT_IP}:8080${NC}"
    echo ""
else
    echo -e "Container started! Check your router DHCP or run:"
    echo -e "   ${CYAN}pct exec ${CT_ID} -- ip -4 addr show eth0${NC}"
    echo -e "Then open ${GREEN}http://<IP>:8080${NC} in your browser."
fi
echo -e "${CYAN}Everything is automated: live monitoring, settings, and Tesla auth!${NC}"
echo -e "${GREEN}===================================================================${NC}"
