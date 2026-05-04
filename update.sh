#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/ir-bridge

green='\033[0;32m'; red='\033[0;31m'; nc='\033[0m'
info()  { printf "${green}[INFO]${nc}  %s\n" "$*"; }
error() { printf "${red}[ERROR]${nc} %s\n" "$*" >&2; }

[[ $EUID -eq 0 ]] || { error "Run with sudo: sudo ./update.sh"; exit 1; }

CONF_FILE=/etc/ir-bridge/env

if [[ ! -d "$INSTALL_DIR" ]]; then
    error "ir-bridge is not installed. Run install.sh first."
    exit 1
fi

if [[ -f "$CONF_FILE" ]] && ! grep -q "^MQTT_PREFIX=" "$CONF_FILE"; then
    default_prefix="ir_remote_$(hostname)"
    echo "MQTT_PREFIX=${default_prefix}" >> "$CONF_FILE"
    info "MQTT_PREFIX was not set — defaulted to '${default_prefix}'. Edit $CONF_FILE to change it."
fi

info "Updating bridge script..."
cp "$SCRIPT_DIR/mqtt_bridge.py" "$INSTALL_DIR/"

info "Updating service file..."
cp "$SCRIPT_DIR/ir-bridge.service" /etc/systemd/system/ir-bridge.service
systemctl daemon-reload

info "Updating Python packages..."
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade paho-mqtt piir

info "Restarting service..."
systemctl restart --no-block ir-bridge

info "Update complete."
systemctl status ir-bridge --no-pager || true

