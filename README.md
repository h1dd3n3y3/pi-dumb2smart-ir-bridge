# pi-dumb2smart-ir-bridge

[![Deploy](https://github.com/h1dd3n3y3/pi-dumb2smart-ir-bridge/actions/workflows/deploy.yml/badge.svg)](https://github.com/h1dd3n3y3/pi-dumb2smart-ir-bridge/actions/workflows/deploy.yml)

Turns any dumb IR-controlled device into a smart home device using a Raspberry Pi Zero 2W, an infrared hat, and Home Assistant. A Raspberry Pi Zero 2W with an IR hat sits near your TV (or any IR-controlled device), listens for commands over your local network, and fires the infrared signal. You control everything from the Home Assistant dashboard — no SSH, no command line required after the initial setup.

> **Future thoughts:** publish on APT so Debian-based users can install with a single `apt install` command.

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

---

## My setup

| Component | Purpose |
|---|---|
| Raspberry Pi Zero 2W | Runs the IR bridge; sends/receives IR signals. One per IR-controlled device. Any Raspberry Pi compatible with the IR hat will work. |
| [ANAVI Infrared pHAT](https://anavi.technology/) | IR hat — IR LED on GPIO 17, IR receiver on GPIO 18. These pins are hardcoded in `mqtt_bridge.py` (`TX_GPIO`, `RX_GPIO`) and must be changed if using a different wiring. |
| Raspberry Pi 5 | Runs Home Assistant and the MQTT broker (Mosquitto). Any machine running Home Assistant and Mosquitto works — it does not have to be a Pi 5. |

---

## Software components

### 1. IR Bridge (`mqtt_bridge.py`) — runs on the IR device

The core service. It:

- Connects to the MQTT broker at startup
- Loads all device JSON files (each file = one remote, e.g. `samsung_tv.json`)
- Pre-loads each device into memory so button presses respond instantly
- Publishes the device and key list so the Home Assistant integration creates button entities automatically
- Listens for button press commands and fires the IR signal via the GPIO pin
- Handles key management commands: record, delete, rename keys; create and delete virtual keys
- Reports recording progress back to Home Assistant via a status topic

It runs as a systemd service (`ir-bridge.service`) and starts automatically on boot.

### 2. MQTT Broker (Mosquitto) — runs on the Home Assistant host

The message router. Every message between Home Assistant and the IR bridge passes through it. Configured for anonymous access on the local network (no credentials required).

### 3. Home Assistant Integration — installed on Pi 5

Provided by the companion repo [pi-dumb2smart-ir](https://github.com/h1dd3n3y3/pi-dumb2smart-ir). It adds IR remote control to Home Assistant via a custom HACS integration.

### 4. Interactive CLI (`remote.py`) — optional, runs on the IR device

A terminal menu for direct local use without Home Assistant. Lets you select a device, record keys, send keys, list keys, and edit existing keys. Useful for initial setup or troubleshooting.

---

## Device files

Device files are JSON files stored in `/var/lib/ir-bridge/` on the IR device. Each file represents one remote (e.g. `samsung_tv.json`) and contains the recorded IR codes for that device.

You can create a device file in two ways:

- **Without Home Assistant** — run `remote.py` directly on the IR device. It provides an interactive menu to create a new device, record keys one by one, and save the result as a JSON file. No broker or integration required.
- **With a pre-made file** — if you already have a compatible JSON file, place it in `/var/lib/ir-bridge/` and trigger a reload.

To load a pre-made file:

1. Place the `.json` file in `/var/lib/ir-bridge/` on the IR device
2. Trigger a reload — no service restart needed:
   - Use the **Reload Devices** button in the Home Assistant integration, or
   - `sudo systemctl restart ir-bridge` as a fallback

The bridge will pick up the new file and publish the keys to Home Assistant automatically.

The expected file format is:

```json
{
  "format": { "...": "piir format block" },
  "keys": {
    "power": "<IR code>",
    "volume_up": "<IR code>"
  }
}
```

### Per-key repeat

Some devices or buttons require a command to be sent more than once to register. This is common for power buttons and other toggle commands where the device expects a double-pulse to confirm intent. Add an optional `key_options` section to the device file:

```json
{
  "format": { "...": "piir format block" },
  "keys": {
    "power": "<IR code>",
    "volume_up": "<IR code>"
  },
  "key_options": {
    "power": {"repeat": 2, "delay_ms": 300}
  }
}
```

- `repeat` — how many times to send the signal (default: `1`)
- `delay_ms` — gap between sends in milliseconds (default: `0`)

After editing the file, trigger a reload from the Home Assistant integration — no service restart needed.

### Virtual keys

Virtual keys are user-defined shortcuts that map a name to an existing key with a fixed repeat count and delay. They appear in Home Assistant as regular button entities alongside the recorded keys.

A common use case is volume stepping — instead of pressing **Volume Up** five times, you create a `vol_up_5_steps` virtual key that sends the signal five times in one press.

Add a `virtual_keys` section to the device file:

```json
{
  "format": { "...": "piir format block" },
  "keys": {
    "power": "<IR code>",
    "volume_up": "<IR code>"
  },
  "key_options": {
    "power": {"repeat": 2, "delay_ms": 300}
  },
  "virtual_keys": {
    "vol_up_5_steps": {"key": "volume_up", "repeat": 5, "delay_ms": 300}
  }
}
```

- `key` — the real recorded key to send
- `repeat` — how many times to send it
- `delay_ms` — gap between sends in milliseconds

Virtual keys can also be created and deleted at runtime via the Home Assistant integration UI — no file editing or service restart required. They are persisted to the device JSON file so they survive bridge restarts.

---

## Deployment (CI/CD)

The repo uses GitHub Actions with a self-hosted runner on Pi 5. On every push to `main`:

1. **Pi 5** — pulls the latest code locally on the runner.
2. **All Pi Zero 2W devices** — Pi 5 runs an Ansible playbook that pulls the latest code and restarts the IR bridge service on every device in the inventory.

Push code on your laptop and all devices are updated automatically within seconds.

---

## Project structure

```
pi-dumb2smart-ir-bridge/
├── mqtt_bridge.py          # IR bridge service (runs on Pi Zero 2W)
├── remote.py               # Interactive CLI (optional, local use)
├── ir-bridge.service       # systemd service unit file
├── install.sh              # One-time setup: installs dependencies, venv, and registers the service
├── uninstall.sh              # Tears down the service and cleans up installed files
├── update.sh                 # Called by CI/CD: copies latest files and restarts the service
├── change_mqtt_topic.sh      # Changes the MQTT prefix: clears old retained messages and restarts with the new prefix
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
