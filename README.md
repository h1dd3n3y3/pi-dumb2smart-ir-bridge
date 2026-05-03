# pi-dumb2smart-ir

[![Deploy](https://github.com/h1dd3n3y3/pi-dumb2smart-ir-bridge/actions/workflows/deploy.yml/badge.svg)](https://github.com/h1dd3n3y3/pi-dumb2smart-ir-bridge/actions/workflows/deploy.yml)

Turns a dumb IR remote into a smart home device. A Raspberry Pi Zero 2W with an IR hat sits near your TV (or any IR-controlled device), listens for commands over your local network, and fires the infrared signal. You control everything from the Home Assistant dashboard — no SSH, no command line required after the initial setup.

---

## How it works — big picture

```
Home Assistant (Pi 5)
       │
       │  MQTT message
       ▼
  Mosquitto broker (Pi 5)
       │
       │  MQTT message
       ▼
  IR Bridge (Pi Zero 2W)
       │
       │  GPIO 17 (IR LED)
       ▼
    Your TV / device
```

1. You press a button in Home Assistant.
2. Home Assistant publishes a short MQTT message to the broker running on your Pi 5.
3. The IR bridge running on Pi Zero 2W receives the message and fires the corresponding infrared signal through the IR LED.
4. Your TV (or other device) responds as if you pressed the real remote.

MQTT is a lightweight messaging protocol designed for IoT — it uses a persistent connection so messages are delivered in milliseconds.

---

## Hardware

| Component | Purpose |
|---|---|
| Raspberry Pi Zero 2W (`pi02-reader`) | Runs the IR bridge; sends/receives IR signals |
| [ANAVI Infrared pHAT](https://anavi.technology/) | IR hat that plugs onto the Pi Zero — IR LED on GPIO 17, IR receiver on GPIO 18 |
| Raspberry Pi 5 (`pi5`) | Runs Home Assistant (Docker) and the MQTT broker (Mosquitto); also acts as the GitHub Actions self-hosted runner |

---

## Software components

### 1. IR Bridge (`mqtt_bridge.py`) — runs on Pi Zero 2W

The core service. It:

- Connects to the MQTT broker on Pi 5 at startup
- Loads all device JSON files (each file = one remote, e.g. `samsung_tv.json`)
- Pre-loads each device into memory so button presses respond instantly
- Publishes MQTT Discovery messages so Home Assistant automatically creates button entities — one per recorded key, per device
- Listens for button press commands and fires the IR signal via the GPIO pin
- Handles key management commands: record a new key, delete a key, rename a key
- Reports recording progress back to Home Assistant via a status topic

It runs as a background process and starts automatically on boot via cron (`@reboot`).

### 2. MQTT Broker (Mosquitto) — runs on Pi 5

The message router. Every message between Home Assistant and the IR bridge passes through it. Configured for anonymous access on the local network (no credentials required). Runs as a native system service on Pi 5.

### 3. Home Assistant Integration (`custom_components/ir_remote`) — installed on Pi 5

A custom HACS integration that adds IR remote control to Home Assistant. Once installed:

- **Button entities** — one button per recorded key (e.g. "Samsung Tv Power", "Samsung Tv Volume Up"). Pressing a button sends the IR signal.
- **Reload Devices button** — tells the bridge to re-read its device files and push any changes to Home Assistant.
- **Recording Status sensor** — shows what the bridge is currently doing: `idle`, `recording`, `done`, `error`, or `timeout`.
- **Services (Actions)** — available under Developer Tools → Actions:
  - `ir_remote.record_key` — put the bridge into recording mode for a new key
  - `ir_remote.delete_key` — permanently remove a key from a device
  - `ir_remote.rename_key` — rename an existing key

### 4. Interactive CLI (`remote.py`) — optional, runs on Pi Zero 2W

A terminal menu for direct local use without Home Assistant. Lets you select a device, record keys, send keys, list keys, and edit existing keys. Useful for initial setup or troubleshooting.

---

## Recording a new key

1. In Home Assistant, go to **Developer Tools → Actions**
2. Call `ir_remote.record_key` with:
   - `device`: the device name (e.g. `samsung_tv`)
   - `key`: the name you want to give this key (e.g. `volume_up`)
3. Watch the **IR Recording Status** sensor — it will show `recording`
4. Point your physical remote at the Pi Zero and press the button **3 times**
5. The sensor will show `done` and a new button entity will appear in Home Assistant automatically

---

## Deployment (CI/CD)

The repo uses GitHub Actions with a self-hosted runner on Pi 5. On every push to `main` or `hacs-integration`:

1. **Pi Zero 2W** — Pi 5 SSHes into the Pi Zero, pulls the latest code, installs any new dependencies, and restarts the IR bridge service.
2. **Pi 5** — pulls the latest code, copies the updated HA integration files into the Home Assistant config directory, and restarts the Home Assistant Docker container.

This means you push code on your laptop and both devices are updated automatically within seconds.

---

## Project structure

```
pi-dumb2smart-ir/
├── mqtt_bridge.py              # IR bridge service (runs on Pi Zero 2W)
├── remote.py                   # Interactive CLI (optional, local use)
├── ir-mqtt-bridge.service      # systemd service file (reference copy)
├── requirements.txt            # Python dependencies
├── <device>.json               # Recorded IR keys per device (gitignored)
├── custom_components/
│   └── ir_remote/              # Home Assistant custom integration
│       ├── __init__.py         # Integration setup, service registration
│       ├── button.py           # Button entities (one per key + reload)
│       ├── sensor.py           # Recording status sensor
│       ├── config_flow.py      # HA setup UI flow
│       ├── const.py            # Shared constants
│       ├── manifest.json       # Integration metadata
│       ├── services.yaml       # Action definitions for HA UI
│       └── translations/
│           └── en.json         # UI strings
├── hacs.json                   # HACS metadata
└── .github/
    └── workflows/
        └── deploy.yml          # CI/CD pipeline
```

---

## Dependencies

- [pigpio](https://abyz.me.uk/rpi/pigpio/) — hardware-timed GPIO daemon (must be running before the bridge starts)
- [PiIR](https://github.com/ts1/PiIR) by [ts1](https://github.com/ts1) — IR signal encoding, decoding, and transmission
- [paho-mqtt](https://pypi.org/project/paho-mqtt/) — MQTT client for Python
- [Mosquitto](https://mosquitto.org/) — MQTT broker
- [Home Assistant](https://www.home-assistant.io/) — smart home platform

---

## Acknowledgements

Built on [PiIR](https://github.com/ts1/PiIR) by [ts1](https://github.com/ts1), which handles the low-level IR signal work via pigpio.
