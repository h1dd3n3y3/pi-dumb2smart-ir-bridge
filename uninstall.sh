#!/usr/bin/env bash
set -euo pipefail

green='\033[0;32m'; red='\033[0;31m'; nc='\033[0m'
info()  { printf "${green}[INFO]${nc}  %s\n" "$*"; }
error() { printf "${red}[ERROR]${nc} %s\n" "$*" >&2; }

[[ $EUID -eq 0 ]] || { error "Run with sudo: sudo ./uninstall.sh"; exit 1; }

info "Stopping and disabling ir-bridge..."
systemctl stop ir-bridge 2>/dev/null || true
systemctl disable ir-bridge 2>/dev/null || true
rm -f /etc/systemd/system/ir-bridge.service
systemctl daemon-reload

info "Removing installed files..."
rm -rf /opt/ir-bridge
rm -rf /etc/ir-bridge

read -rp "Remove remote data from /var/lib/ir-bridge? This deletes all learned IR keys. [y/N]: " confirm
if [[ "${confirm,,}" == "y" ]]; then
    rm -rf /var/lib/ir-bridge
    info "Data removed."
else
    info "Data kept at /var/lib/ir-bridge."
fi

info "Stopping and disabling pigpiod..."
systemctl stop pigpiod 2>/dev/null || true
systemctl disable pigpiod 2>/dev/null || true
rm -f /etc/systemd/system/pigpiod.service
systemctl daemon-reload

read -rp "Remove pigpiod? [y/N]: " confirm
if [[ "${confirm,,}" == "y" ]]; then
    if command -v pigpiod &>/dev/null && dpkg -l pigpiod &>/dev/null 2>&1; then
        apt-get remove -y pigpiod
    else
        rm -f /usr/local/bin/pigpiod /usr/local/bin/pig2vcd /usr/local/bin/pigs
        rm -f /usr/local/lib/libpigpio*.so /usr/local/lib/libpigpio*.a
        rm -f /usr/local/include/pigpio*.h
    fi
    info "pigpiod removed."
else
    info "pigpiod kept."
fi

info "Uninstall complete."
