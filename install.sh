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

# Prevent needrestart from auto-restarting sshd mid-install
export NEEDRESTART_MODE=l

info "Installing system packages..."
apt-get update -qq
apt-get install -y python3-venv python3-dev python3-setuptools gcc wget unzip make mosquitto-clients

if command -v pigpiod &>/dev/null; then
    info "pigpiod already installed at $(command -v pigpiod) — skipping."
elif apt-get install -y pigpiod; then
    info "pigpiod installed via apt."
else
    info "pigpiod not in apt — building from source..."
    cd /tmp
    wget -q https://github.com/joan2937/pigpio/archive/master.zip
    unzip -q master.zip
    make -C pigpio-master -j"$(nproc)"
    make -C pigpio-master install
    rm -rf /tmp/pigpio-master /tmp/master.zip
    cd "$SCRIPT_DIR"
    info "pigpiod built and installed from source."
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
    read -rp "MQTT broker host (HA server IP or hostname): " mqtt_host
    while [[ -z "$mqtt_host" ]]; do
        read -rp "MQTT broker host (HA server IP or hostname): " mqtt_host
    done
    read -rp "MQTT broker port (default is 1883): " mqtt_port
    mqtt_port="${mqtt_port:-1883}"

    default_prefix="ir_remote_$(hostname | tr '-' '_')"
    printf "\nEach bridge must have a unique MQTT topic prefix.\n"
    printf "You will need to enter this exact value when adding this bridge in Home Assistant.\n"
    read -rp "MQTT topic prefix for this bridge [${default_prefix}]: " mqtt_prefix
    mqtt_prefix="${mqtt_prefix:-$default_prefix}"

    cat > "$CONF_DIR/env" <<EOF
MQTT_HOST=$mqtt_host
MQTT_PORT=$mqtt_port
MQTT_PREFIX=$mqtt_prefix
IR_DATA_DIR=$DATA_DIR
EOF
    info "Config written to $CONF_DIR/env"
    printf "\n"
    printf "${green}[IMPORTANT]${nc} Your MQTT topic prefix is: ${green}${mqtt_prefix}${nc}\n"
    printf "            You will need this when setting up the Home Assistant integration.\n"
    printf "            You can always find it later in: $CONF_DIR/env\n\n"
else
    info "Config already exists at $CONF_DIR/env — skipping. Edit it to change settings."
fi

info "Enabling pigpiod..."
if ! systemctl cat pigpiod &>/dev/null; then
    info "No pigpiod.service found — creating one for source-installed pigpiod..."
    cat > /etc/systemd/system/pigpiod.service <<'EOF'
[Unit]
Description=Daemon required to control GPIO pins via pigpio
After=network.target

[Service]
ExecStart=/usr/local/bin/pigpiod -l
ExecStop=/bin/kill -INT $MAINPID
Type=forking

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
fi

# Kill any orphaned pigpiod started outside systemd so systemd can own the pid file
killall pigpiod 2>/dev/null || true

systemctl enable --now pigpiod

info "Enabling ir-bridge..."
systemctl enable --now ir-bridge

info ""
info "Installation complete."
info "  Logs:   journalctl -u ir-bridge -f"
info "  Config: $CONF_DIR/env"
info "  Data:   $DATA_DIR"
