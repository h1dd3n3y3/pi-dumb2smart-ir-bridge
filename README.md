# pi-dumb2smart-ir-bridge

[![Deploy](https://github.com/h1dd3n3y3/pi-dumb2smart-ir-bridge/actions/workflows/deploy.yml/badge.svg)](https://github.com/h1dd3n3y3/pi-dumb2smart-ir-bridge/actions/workflows/deploy.yml)

Turns any dumb IR-controlled device into a smart home device using a Raspberry Pi Zero 2W, an infrared hat, and Home Assistant. A Raspberry Pi Zero 2W with an IR hat sits near your TV (or any IR-controlled device), listens for commands over your local network, and fires the infrared signal. You control everything from the Home Assistant dashboard — no SSH, no command line required after the initial setup.

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

It runs as a systemd service (`ir-bridge.service`) and starts automatically on boot.

### 2. MQTT Broker (Mosquitto) — runs on Pi 5

The message router. Every message between Home Assistant and the IR bridge passes through it. Configured for anonymous access on the local network (no credentials required). Runs as a native system service on Pi 5.

### 3. Home Assistant Integration — installed on Pi 5

Provided by the companion repo [pi-dumb2smart-ir](https://github.com/h1dd3n3y3/pi-dumb2smart-ir). It adds IR remote control to Home Assistant via a custom HACS integration.

### 4. Interactive CLI (`remote.py`) — optional, runs on Pi Zero 2W

A terminal menu for direct local use without Home Assistant. Lets you select a device, record keys, send keys, list keys, and edit existing keys. Useful for initial setup or troubleshooting.

---

## Managing remotes and keys

All management is done through the Home Assistant UI provided by the [companion integration](https://github.com/h1dd3n3y3/pi-dumb2smart-ir).

### Creating a remote

1. Go to **Settings → Devices & Services** and open the IR Remote integration
2. Open the bridge device
3. From there you can create, delete, or rename a remote — each remote represents one physical IR-controlled device (e.g. `samsung_tv`)

### Recording a key

1. Open a remote inside the integration
2. From there you can record, delete, or rename individual keys
3. To record: select **Record** and give the key a name
4. All bridges currently registered will enter recording mode — point your physical remote at the correct Pi Zero and press the button 2–3 times
5. Once recorded, a new button entity appears in Home Assistant automatically

---

## Deployment (CI/CD)

The repo uses GitHub Actions with a self-hosted runner on Pi 5. On every push to `main`:

1. **Pi Zero 2W** — Pi 5 SSHes into the Pi Zero, pulls the latest code, installs any new dependencies, and restarts the IR bridge service.
2. **Pi 5** — pulls the latest code locally on the runner.

This means you push code on your laptop and both devices are updated automatically within seconds.

---

## Project structure

```
pi-dumb2smart-ir-bridge/
├── mqtt_bridge.py          # IR bridge service (runs on Pi Zero 2W)
├── remote.py               # Interactive CLI (optional, local use)
├── ir-bridge.service       # systemd service unit file
├── install.sh              # One-time setup: installs dependencies, venv, and registers the service
├── uninstall.sh            # Tears down the service and cleans up installed files
├── update.sh               # Called by CI/CD: copies latest files and restarts the service
├── requirements.txt        # Python dependencies
└── .github/
    └── workflows/
        └── deploy.yml      # CI/CD pipeline
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
