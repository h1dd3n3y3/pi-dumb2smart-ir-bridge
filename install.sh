#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/ir-bridge
DATA_DIR=/var/lib/ir-bridge
CONF_DIR=/etc/ir-bridge
SERVICE_FILE=/etc/systemd/system/ir-bridge.service

green='\033[0;32m'; red='\033[0;31m'; nc='\033[0m'
info()  { printf "${green}[INFO]${nc}  %s\n" "$*"; }
error() { printf "${red}[ERROR]${nc} %s\n" "$*" >&2; }

[[ $EUID -eq 0 ]] || { error "Run with sudo: sudo ./install.sh"; exit 1; }

info "Installing system packages..."
apt-get update -qq
apt-get install -y python3-venv python3-dev gcc

if command -v pigpiod &>/dev/null; then
    info "pigpiod already installed at $(command -v pigpiod) — skipping."
else
    apt-get install -y pigpiod || { error "pigpiod not found in apt. Install it from source: https://github.com/joan2937/pigpio"; exit 1; }
fi

info "Creating directories..."
install -d "$INSTALL_DIR" "$DATA_DIR" "$CONF_DIR"

info "Setting up Python environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet paho-mqtt piir

info "Installing bridge script..."
cp "$SCRIPT_DIR/mqtt_bridge.py" "$INSTALL_DIR/"

info "Installing systemd service..."
cp "$SCRIPT_DIR/ir-bridge.service" "$SERVICE_FILE"
systemctl daemon-reload

if [[ ! -f "$CONF_DIR/env" ]]; then
    read -rp "MQTT broker host [homeassistant.local]: " mqtt_host
    mqtt_host="${mqtt_host:-homeassistant.local}"
    read -rp "MQTT broker port [1883]: " mqtt_port
    mqtt_port="${mqtt_port:-1883}"
    cat > "$CONF_DIR/env" <<EOF
MQTT_HOST=$mqtt_host
MQTT_PORT=$mqtt_port
IR_DATA_DIR=$DATA_DIR
EOF
    info "Config written to $CONF_DIR/env"
else
    info "Config already exists at $CONF_DIR/env — skipping. Edit it to change settings."
fi

info "Enabling pigpiod..."
systemctl enable --now pigpiod

info "Enabling ir-bridge..."
systemctl enable --now ir-bridge

info ""
info "Installation complete."
info "  Logs:   journalctl -u ir-bridge -f"
info "  Config: $CONF_DIR/env"
info "  Data:   $DATA_DIR"
