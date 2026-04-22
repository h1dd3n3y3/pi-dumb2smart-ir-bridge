#!/usr/bin/env python3
"""MQTT bridge for ANAVI IR pHAT — anonymous broker connection.

Connects to the MQTT broker without credentials (anonymous access).
Publishes:
  - ir_remote/devices          retained JSON device+key map
  - homeassistant/button/..    MQTT Discovery configs
  - ir_remote/availability     online/offline LWT
  - ir_remote/record/status    recording progress/result

Subscribes to:
  - ir_remote/<device>/send    payload = key name → fires IR signal
  - ir_remote/reload           re-read JSON files and republish
  - ir_remote/record/start     payload = {"device":..,"key":..} → record key
  - ir_remote/key/delete       payload = {"device":..,"key":..} → delete key
  - ir_remote/key/rename       payload = {"device":..,"old":..,"new":..} → rename key
  - ir_remote/device/create    payload = {"device":..} → create new device JSON
  - ir_remote/device/delete    payload = {"device":..} → delete device JSON
  - ir_remote/device/rename    payload = {"old":..,"new":..} → rename device JSON

Environment variables:
    MQTT_HOST   broker hostname/IP  (default: pi5.local)
    MQTT_PORT   broker port         (default: 1883)
"""

import glob
import json
import os
import subprocess
import sys
import threading

import paho.mqtt.client as mqtt
import piir  # type: ignore

TX_GPIO = 17
RX_GPIO = 18

MQTT_HOST = os.getenv("MQTT_HOST", "pi5.local")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

DISCOVERY_PREFIX = "homeassistant"
BASE_TOPIC = "ir_remote"
AVAILABILITY_TOPIC = f"{BASE_TOPIC}/availability"
DEVICES_TOPIC = f"{BASE_TOPIC}/devices"
RELOAD_TOPIC = f"{BASE_TOPIC}/reload"
RECORD_START_TOPIC = f"{BASE_TOPIC}/record/start"
RECORD_STATUS_TOPIC = f"{BASE_TOPIC}/record/status"
KEY_DELETE_TOPIC = f"{BASE_TOPIC}/key/delete"
KEY_RENAME_TOPIC = f"{BASE_TOPIC}/key/rename"
DEVICE_CREATE_TOPIC = f"{BASE_TOPIC}/device/create"
DEVICE_DELETE_TOPIC = f"{BASE_TOPIC}/device/delete"
DEVICE_RENAME_TOPIC = f"{BASE_TOPIC}/device/rename"

_remotes: dict = {}


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

def _script_dir() -> str:
    return os.getenv("IR_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))


def load_all_devices() -> dict:
    devices = {}
    for path in sorted(glob.glob(os.path.join(_script_dir(), "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path) as f:
                data = json.load(f)
            keys = list(data.get("keys", {}).keys())
            devices[name] = keys
        except Exception as exc:
            print(f"[WARN] Skipping {path}: {exc}")
    return devices


def _load_raw(device_path: str) -> dict:
    try:
        with open(device_path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_raw(device_path: str, data: dict) -> None:
    with open(device_path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# MQTT Discovery
# ---------------------------------------------------------------------------

def publish_discovery(client: mqtt.Client, devices: dict) -> None:
    for device_name, keys in devices.items():
        device_id = f"ir_{device_name}"
        command_topic = f"{BASE_TOPIC}/{device_name}/send"
        for key in keys:
            unique_id = f"{device_id}_{key}"
            config = {
                "name": key.replace("_", " ").title(),
                "unique_id": unique_id,
                "command_topic": command_topic,
                "payload_press": key,
                "availability_topic": AVAILABILITY_TOPIC,
                "device": {
                    "identifiers": [device_id],
                    "name": device_name.replace("_", " ").title(),
                    "model": "ANAVI IR pHAT",
                    "manufacturer": "ANAVI",
                },
            }
            topic = f"{DISCOVERY_PREFIX}/button/{unique_id}/config"
            client.publish(topic, json.dumps(config), retain=True)

    print(f"[INFO] Discovery published for {len(devices)} device(s).")


def _build_remotes(devices: dict) -> None:
    global _remotes
    _remotes = {}
    for device_name, keys in devices.items():
        if not keys:
            continue
        device_path = os.path.join(_script_dir(), f"{device_name}.json")
        try:
            _remotes[device_name] = piir.Remote(device_path, TX_GPIO)
        except Exception as exc:
            print(f"[WARN] Could not load remote for {device_name}: {exc}")
    print(f"[INFO] Remotes cached for: {list(_remotes)}")


def _republish_devices(client: mqtt.Client) -> None:
    devices = load_all_devices()
    client.publish(DEVICES_TOPIC, json.dumps(devices), retain=True)
    if devices:
        publish_discovery(client, devices)
        for device_name in devices:
            client.subscribe(f"{BASE_TOPIC}/{device_name}/send")
        _build_remotes(devices)
    print("[INFO] Device list reloaded.")


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def _handle_record(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"]
        key_name = data["key"]
    except Exception:
        client.publish(RECORD_STATUS_TOPIC, json.dumps({"status": "error", "message": "Invalid payload"}))
        return

    device_path = os.path.join(_script_dir(), f"{device_name}.json")

    # For new devices with no keys, remove the placeholder so piir auto-detects protocol
    raw = _load_raw(device_path)
    is_new_device = not raw.get("keys")
    if is_new_device and os.path.exists(device_path):
        os.remove(device_path)

    def _record():
        client.publish(RECORD_STATUS_TOPIC, json.dumps({"status": "recording", "key": key_name}))
        print(f"[INFO] Recording '{key_name}' for '{device_name}'...")
        cmd = ["piir", "record", "--gpio", str(RX_GPIO), "--file", device_path, key_name]
        env = os.environ.copy()
        env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
        try:
            result = subprocess.run(cmd, timeout=30, env=env)
            if result.returncode == 0:
                _republish_devices(client)
                client.publish(RECORD_STATUS_TOPIC, json.dumps({"status": "done", "key": key_name}))
                print(f"[INFO] Recorded '{key_name}'")
            else:
                if is_new_device and not os.path.exists(device_path):
                    _save_raw(device_path, {"keys": {}})
                client.publish(RECORD_STATUS_TOPIC, json.dumps({"status": "error", "key": key_name}))
        except subprocess.TimeoutExpired:
            if is_new_device and not os.path.exists(device_path):
                _save_raw(device_path, {"keys": {}})
            client.publish(RECORD_STATUS_TOPIC, json.dumps({"status": "timeout", "key": key_name}))
            print(f"[WARN] Recording timed out for '{key_name}'")
        except Exception as exc:
            if is_new_device and not os.path.exists(device_path):
                _save_raw(device_path, {"keys": {}})
            client.publish(RECORD_STATUS_TOPIC, json.dumps({"status": "error", "key": key_name}))
            print(f"[ERROR] Recording failed for '{key_name}': {exc}")

    thread = threading.Thread(target=_record, daemon=True)
    thread.start()


def _handle_delete(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"]
        key_name = data["key"]
    except Exception:
        return

    device_path = os.path.join(_script_dir(), f"{device_name}.json")
    raw = _load_raw(device_path)
    keys = raw.get("keys", {})
    actual_key = next((k for k in keys if k.lower() == key_name.lower()), None)
    if actual_key:
        del keys[actual_key]
        raw["keys"] = keys
        _save_raw(device_path, raw)
        _republish_devices(client)
        print(f"[INFO] Deleted '{actual_key}' from '{device_name}'")


def _handle_rename(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"]
        old_name = data["old"]
        new_name = data["new"]
    except Exception:
        return

    device_path = os.path.join(_script_dir(), f"{device_name}.json")
    raw = _load_raw(device_path)
    keys = raw.get("keys", {})
    actual_old = next((k for k in keys if k.lower() == old_name.lower()), None)
    actual_new = next((k for k in keys if k.lower() == new_name.lower()), None)
    if actual_old and not actual_new:
        keys[new_name] = keys.pop(actual_old)
        raw["keys"] = keys
        _save_raw(device_path, raw)
        _republish_devices(client)
        print(f"[INFO] Renamed '{old_name}' -> '{new_name}' in '{device_name}'")


def _handle_create_device(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"].strip().lower().replace(" ", "_")
    except Exception:
        return

    if not device_name:
        return

    device_path = os.path.join(_script_dir(), f"{device_name}.json")
    if os.path.exists(device_path):
        print(f"[INFO] Device '{device_name}' already exists, skipping")
        return

    _save_raw(device_path, {"keys": {}})
    _republish_devices(client)
    print(f"[INFO] Created device '{device_name}'")


def _handle_delete_device(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"].strip().lower().replace(" ", "_")
    except Exception:
        return

    if not device_name:
        return

    device_path = os.path.join(_script_dir(), f"{device_name}.json")
    if not os.path.exists(device_path):
        print(f"[INFO] Device '{device_name}' not found, skipping delete")
        return

    os.remove(device_path)
    _republish_devices(client)
    print(f"[INFO] Deleted device '{device_name}'")


def _handle_rename_device(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        old_name = data["old"].strip().lower().replace(" ", "_")
        new_name = data["new"].strip().lower().replace(" ", "_")
    except Exception:
        return

    if not old_name or not new_name:
        return

    old_path = os.path.join(_script_dir(), f"{old_name}.json")
    new_path = os.path.join(_script_dir(), f"{new_name}.json")

    if not os.path.exists(old_path):
        print(f"[INFO] Device '{old_name}' not found, skipping rename")
        return

    if os.path.exists(new_path):
        print(f"[INFO] Device '{new_name}' already exists, skipping rename")
        return

    os.rename(old_path, new_path)
    _republish_devices(client)
    print(f"[INFO] Renamed device '{old_name}' -> '{new_name}'")


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------

def on_connect(client, userdata, _flags, rc, _properties=None):
    if rc != 0:
        print(f"[ERROR] MQTT connect failed (rc={rc}). Retrying...")
        return

    print(f"[INFO] Connected to {MQTT_HOST}:{MQTT_PORT}")
    client.publish(AVAILABILITY_TOPIC, "online", retain=True)

    devices = load_all_devices()
    if not devices:
        print("[WARN] No device JSON files found — nothing to publish.")
    else:
        client.publish(DEVICES_TOPIC, json.dumps(devices), retain=True)
        publish_discovery(client, devices)
        _build_remotes(devices)
        for device_name in devices:
            topic = f"{BASE_TOPIC}/{device_name}/send"
            client.subscribe(topic)
            print(f"[INFO] Subscribed to {topic}")

    for topic in (RELOAD_TOPIC, RECORD_START_TOPIC, KEY_DELETE_TOPIC, KEY_RENAME_TOPIC, DEVICE_CREATE_TOPIC, DEVICE_DELETE_TOPIC, DEVICE_RENAME_TOPIC):
        client.subscribe(topic)
        print(f"[INFO] Subscribed to {topic}")


def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode().strip()

    if topic == RELOAD_TOPIC:
        print("[INFO] Reload requested.")
        _republish_devices(client)

    elif topic == RECORD_START_TOPIC:
        _handle_record(client, payload)

    elif topic == KEY_DELETE_TOPIC:
        _handle_delete(client, payload)

    elif topic == KEY_RENAME_TOPIC:
        _handle_rename(client, payload)

    elif topic == DEVICE_CREATE_TOPIC:
        _handle_create_device(client, payload)

    elif topic == DEVICE_DELETE_TOPIC:
        _handle_delete_device(client, payload)

    elif topic == DEVICE_RENAME_TOPIC:
        _handle_rename_device(client, payload)

    else:
        parts = topic.split("/")
        if len(parts) == 3:
            device_name = parts[1]
            remote = _remotes.get(device_name)
            if remote is None:
                print(f"[ERROR] No cached remote for '{device_name}'")
                return
            try:
                remote.send(payload)
                print(f"[INFO] Sent: {device_name}/{payload}")
            except Exception as exc:
                print(f"[ERROR] Failed to send {device_name}/{payload}: {exc}")


def on_disconnect(client, userdata, rc, properties=None, reasoncode=None):
    if rc != 0:
        print(f"[WARN] Unexpected disconnect (rc={rc}). paho will reconnect.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    except Exception as exc:
        print(f"[ERROR] Cannot connect to {MQTT_HOST}:{MQTT_PORT}: {exc}")
        sys.exit(1)

    client.loop_forever()


if __name__ == "__main__":
    main()
