#!/usr/bin/env bash
set -euo pipefail

CONF_FILE=/etc/ir-bridge/env

green='\033[0;32m'; red='\033[0;31m'; yellow='\033[1;33m'; nc='\033[0m'
info()  { printf "${green}[INFO]${nc}  %s\n" "$*"; }
warn()  { printf "${yellow}[WARN]${nc}  %s\n" "$*"; }
error() { printf "${red}[ERROR]${nc} %s\n" "$*" >&2; }

[[ $EUID -eq 0 ]] || { error "Run with sudo: sudo ./change_mqtt_topic.sh"; exit 1; }

[[ -f "$CONF_FILE" ]] || { error "Config file not found: $CONF_FILE — is the bridge installed?"; exit 1; }

command -v mosquitto_pub &>/dev/null || { info "Installing mosquitto-clients..."; apt-get install -y mosquitto-clients; }

source "$CONF_FILE"
OLD_PREFIX="${MQTT_PREFIX:-ir_remote}"
MQTT_HOST="${MQTT_HOST:-localhost}"
MQTT_PORT="${MQTT_PORT:-1883}"

info "Current MQTT prefix: ${OLD_PREFIX}"
printf "\n"

read -rp "New MQTT topic prefix: " NEW_PREFIX
while [[ -z "$NEW_PREFIX" ]]; do
    read -rp "New MQTT topic prefix: " NEW_PREFIX
done

[[ "$NEW_PREFIX" == "$OLD_PREFIX" ]] && { warn "New prefix is the same as current — nothing to do."; exit 0; }

printf "\n"
info "Stopping ir-bridge service..."
systemctl stop ir-bridge

info "Clearing retained messages for old prefix '${OLD_PREFIX}'..."
for topic in \
    "${OLD_PREFIX}/devices" \
    "${OLD_PREFIX}/availability" \
    "${OLD_PREFIX}/record/status"; do
    mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$topic" -r -n
done
info "Old retained messages cleared."

info "Updating config..."
sed -i "s|^MQTT_PREFIX=.*|MQTT_PREFIX=${NEW_PREFIX}|" "$CONF_FILE"
grep -q "^MQTT_PREFIX=" "$CONF_FILE" || echo "MQTT_PREFIX=${NEW_PREFIX}" >> "$CONF_FILE"

info "Starting ir-bridge service with new prefix '${NEW_PREFIX}'..."
systemctl start ir-bridge

printf "\n"
printf "${green}[DONE]${nc} Prefix changed: ${yellow}${OLD_PREFIX}${nc} → ${green}${NEW_PREFIX}${nc}\n"
printf "       Update the Home Assistant integration entry to use: ${green}${NEW_PREFIX}${nc}\n"
printf "       New config saved to: $CONF_FILE\n\n"
