#!/usr/bin/env python3
"""MQTT bridge for ANAVI IR pHAT — anonymous broker connection.

Connects to the MQTT broker without credentials (anonymous access).
Publishes:
  - <prefix>/devices                   retained JSON device+key map (includes virtual key names)
  - <prefix>/key_options               retained JSON per-key repeat options
  - <prefix>/virtual_keys              retained JSON per-device virtual key definitions
  - <prefix>/availability              online/offline LWT
  - <prefix>/record/status             recording progress/result

Subscribes to:
  - <prefix>/<device>/send             payload = key name → fires IR signal
  - <prefix>/reload                    re-read JSON files and republish
  - <prefix>/record/start              payload = {"device":..,"key":..} → record key
  - <prefix>/key/delete                payload = {"device":..,"key":..} → delete key
  - <prefix>/key/rename                payload = {"device":..,"old":..,"new":..} → rename key
  - <prefix>/key/set_options           payload = {"device":..,"key":..,"repeat":N,"delay_ms":N} → set repeat
  - <prefix>/virtual_key/create        payload = {"device":..,"name":..,"key":..,"repeat":N,"delay_ms":N} → create virtual key
  - <prefix>/virtual_key/delete        payload = {"device":..,"name":..} → delete virtual key
  - <prefix>/device/create             payload = {"device":..} → create new device JSON
  - <prefix>/device/delete             payload = {"device":..} → delete device JSON
  - <prefix>/device/rename             payload = {"old":..,"new":..} → rename device JSON

Environment variables:
    MQTT_HOST      broker hostname/IP  (default: pi5.local)
    MQTT_PORT      broker port         (default: 1883)
    MQTT_PREFIX    MQTT topic prefix   (default: ir_remote) — must be unique per bridge
"""

import glob
import json
import os
import subprocess
import sys
import threading
import time

import paho.mqtt.client as mqtt
import piir  # type: ignore

TX_GPIO = 17
RX_GPIO = 18

MQTT_HOST = os.getenv("MQTT_HOST", "pi5.local")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DATA_DIR = os.getenv("IR_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))

DISCOVERY_PREFIX = "homeassistant"
BASE_TOPIC = os.getenv("MQTT_PREFIX", "ir_remote")
AVAILABILITY_TOPIC = f"{BASE_TOPIC}/availability"
DEVICES_TOPIC = f"{BASE_TOPIC}/devices"
RELOAD_TOPIC = f"{BASE_TOPIC}/reload"
RECORD_START_TOPIC = f"{BASE_TOPIC}/record/start"
RECORD_STATUS_TOPIC = f"{BASE_TOPIC}/record/status"
KEY_DELETE_TOPIC = f"{BASE_TOPIC}/key/delete"
KEY_RENAME_TOPIC = f"{BASE_TOPIC}/key/rename"
KEY_SET_OPTIONS_TOPIC = f"{BASE_TOPIC}/key/set_options"
KEY_OPTIONS_TOPIC = f"{BASE_TOPIC}/key_options"
VIRTUAL_KEY_CREATE_TOPIC = f"{BASE_TOPIC}/virtual_key/create"
VIRTUAL_KEY_DELETE_TOPIC = f"{BASE_TOPIC}/virtual_key/delete"
VIRTUAL_KEYS_TOPIC = f"{BASE_TOPIC}/virtual_keys"
DEVICE_CREATE_TOPIC = f"{BASE_TOPIC}/device/create"
DEVICE_DELETE_TOPIC = f"{BASE_TOPIC}/device/delete"
DEVICE_RENAME_TOPIC = f"{BASE_TOPIC}/device/rename"

_remotes: dict = {}
_key_options: dict = {}  # {device_name: {key_name: {"repeat": N, "delay_ms": N}}}
_virtual_keys: dict = {}  # {device_name: {vkey_name: {"key": base_key, "repeat": N, "delay_ms": N}}}

# Tracks whether the startup sequence (cleanup + subscribe) has completed for
# the current connection. Reset on each reconnect so subscriptions are
# re-established and stale discovery is re-evaluated.
_startup: dict = {"done": False, "timer": None, "lock": threading.Lock()}


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

def load_all_devices() -> dict:
    devices = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path) as f:
                data = json.load(f)
            keys = list(data.get("keys", {}).keys())
            virtual = list(data.get("virtual_keys", {}).keys())
            devices[name] = keys + virtual
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

def _clear_device_discovery(client: mqtt.Client, device_name: str, keys: list) -> None:
    """Publish empty retained messages to remove a device's discovery entries."""
    for key in keys:
        uid = f"ir_{device_name}_{key}"
        client.publish(f"{DISCOVERY_PREFIX}/button/{uid}/config", "", retain=True)
    if keys:
        print(f"[INFO] Cleared discovery for '{device_name}' ({len(keys)} key(s))")



def _build_remotes(devices: dict) -> None:
    global _remotes, _key_options, _virtual_keys
    _remotes = {}
    _key_options = {}
    _virtual_keys = {}
    for device_name, keys in devices.items():
        if not keys:
            continue
        device_path = os.path.join(DATA_DIR, f"{device_name}.json")
        try:
            _remotes[device_name] = piir.Remote(device_path, TX_GPIO)
            raw = _load_raw(device_path)
            opts = raw.get("key_options", {})
            if opts:
                _key_options[device_name] = opts
            vkeys = raw.get("virtual_keys", {})
            if vkeys:
                _virtual_keys[device_name] = vkeys
        except Exception as exc:
            print(f"[WARN] Could not load remote for {device_name}: {exc}")
    print(f"[INFO] Remotes cached for: {list(_remotes)}")


def _publish_virtual_keys(client: mqtt.Client) -> None:
    all_vkeys = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        vkeys = _load_raw(path).get("virtual_keys", {})
        if vkeys:
            all_vkeys[name] = vkeys
    client.publish(VIRTUAL_KEYS_TOPIC, json.dumps(all_vkeys), retain=True)


def _publish_key_options(client: mqtt.Client) -> None:
    all_opts = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        opts = _load_raw(path).get("key_options", {})
        if opts:
            all_opts[name] = opts
    client.publish(KEY_OPTIONS_TOPIC, json.dumps(all_opts), retain=True)


def _handle_set_options(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"]
        key_name = data["key"]
        repeat = int(data.get("repeat", 1))
        delay_ms = int(data.get("delay_ms", 0))
    except Exception:
        return

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")
    if not os.path.exists(device_path):
        return

    raw = _load_raw(device_path)
    if repeat > 1:
        raw.setdefault("key_options", {})[key_name] = {"repeat": repeat, "delay_ms": delay_ms}
    else:
        raw.get("key_options", {}).pop(key_name, None)
        if not raw.get("key_options"):
            raw.pop("key_options", None)
    _save_raw(device_path, raw)

    if device_name in _remotes:
        opts = raw.get("key_options", {})
        if opts:
            _key_options[device_name] = opts
        else:
            _key_options.pop(device_name, None)

    _publish_key_options(client)
    print(f"[INFO] Options updated for '{device_name}/{key_name}': repeat={repeat}, delay_ms={delay_ms}")


def _handle_create_virtual_key(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"]
        vkey_name = data["name"]
        base_key = data["key"]
        repeat = int(data.get("repeat", 1))
        delay_ms = int(data.get("delay_ms", 0))
    except Exception:
        return

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")
    if not os.path.exists(device_path):
        return

    raw = _load_raw(device_path)
    if base_key not in raw.get("keys", {}):
        print(f"[WARN] Base key '{base_key}' not found in '{device_name}'")
        return

    raw.setdefault("virtual_keys", {})[vkey_name] = {"key": base_key, "repeat": repeat, "delay_ms": delay_ms}
    _save_raw(device_path, raw)
    _virtual_keys.setdefault(device_name, {})[vkey_name] = {"key": base_key, "repeat": repeat, "delay_ms": delay_ms}
    _republish_devices(client)
    print(f"[INFO] Created virtual key '{vkey_name}' for '{device_name}' (base={base_key}, repeat={repeat}, delay_ms={delay_ms})")


def _handle_delete_virtual_key(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        device_name = data["device"]
        vkey_name = data["name"]
    except Exception:
        return

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")
    if not os.path.exists(device_path):
        return

    raw = _load_raw(device_path)
    vkeys = raw.get("virtual_keys", {})
    if vkey_name not in vkeys:
        return

    del vkeys[vkey_name]
    if not vkeys:
        raw.pop("virtual_keys", None)
    else:
        raw["virtual_keys"] = vkeys
    _save_raw(device_path, raw)

    if device_name in _virtual_keys:
        _virtual_keys[device_name].pop(vkey_name, None)
        if not _virtual_keys[device_name]:
            del _virtual_keys[device_name]

    _republish_devices(client)
    print(f"[INFO] Deleted virtual key '{vkey_name}' from '{device_name}'")


def _republish_devices(client: mqtt.Client) -> None:
    devices = load_all_devices()
    client.publish(DEVICES_TOPIC, json.dumps(devices), retain=True)
    if devices:
        for device_name in devices:
            client.subscribe(f"{BASE_TOPIC}/{device_name}/send")
        _build_remotes(devices)
    _publish_key_options(client)
    _publish_virtual_keys(client)
    print("[INFO] Device list reloaded.")


# ---------------------------------------------------------------------------
# Startup sequence
# ---------------------------------------------------------------------------

def _do_startup(client: mqtt.Client, prev_devices: dict) -> None:
    """Complete startup: clear stale discovery topics then publish current state."""
    with _startup["lock"]:
        if _startup["done"]:
            return
        _startup["done"] = True

    if _startup["timer"]:
        _startup["timer"].cancel()
        _startup["timer"] = None

    current_devices = load_all_devices()

    # Clear any lingering MQTT discovery topics from all known devices
    all_known = {**prev_devices, **current_devices}
    for device_name, keys in all_known.items():
        if keys:
            _clear_device_discovery(client, device_name, keys)

    if not current_devices:
        print("[WARN] No device JSON files found — nothing to publish.")
        client.publish(DEVICES_TOPIC, json.dumps({}), retain=True)
    else:
        client.publish(DEVICES_TOPIC, json.dumps(current_devices), retain=True)
        _build_remotes(current_devices)
        _publish_key_options(client)
        _publish_virtual_keys(client)
        for device_name in current_devices:
            topic = f"{BASE_TOPIC}/{device_name}/send"
            client.subscribe(topic)
            print(f"[INFO] Subscribed to {topic}")

    for topic in (RELOAD_TOPIC, RECORD_START_TOPIC, KEY_DELETE_TOPIC, KEY_RENAME_TOPIC,
                  KEY_SET_OPTIONS_TOPIC, VIRTUAL_KEY_CREATE_TOPIC, VIRTUAL_KEY_DELETE_TOPIC,
                  DEVICE_CREATE_TOPIC, DEVICE_DELETE_TOPIC, DEVICE_RENAME_TOPIC):
        client.subscribe(topic)
        print(f"[INFO] Subscribed to {topic}")


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

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")

    # For new devices with no keys, remove the placeholder so piir auto-detects protocol
    raw = _load_raw(device_path)
    is_new_device = not raw.get("keys")
    if is_new_device and os.path.exists(device_path):
        os.remove(device_path)

    def _record():
        client.publish(RECORD_STATUS_TOPIC, json.dumps({"status": "recording", "key": key_name}))
        print(f"[INFO] Recording '{key_name}' for '{device_name}'...")
        piir_bin = os.path.join(os.path.dirname(sys.executable), "piir")
        cmd = [piir_bin, "record", "--gpio", str(RX_GPIO), "--file", device_path, key_name]
        env = os.environ.copy()
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

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")
    raw = _load_raw(device_path)
    keys = raw.get("keys", {})
    actual_key = next((k for k in keys if k.lower() == key_name.lower()), None)
    if actual_key:
        del keys[actual_key]
        raw["keys"] = keys
        _save_raw(device_path, raw)
        _clear_device_discovery(client, device_name, [actual_key])
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

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")
    raw = _load_raw(device_path)
    keys = raw.get("keys", {})
    actual_old = next((k for k in keys if k.lower() == old_name.lower()), None)
    actual_new = next((k for k in keys if k.lower() == new_name.lower()), None)
    if actual_old and not actual_new:
        keys[new_name] = keys.pop(actual_old)
        raw["keys"] = keys
        _save_raw(device_path, raw)
        _clear_device_discovery(client, device_name, [actual_old])
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

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")
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

    device_path = os.path.join(DATA_DIR, f"{device_name}.json")
    if not os.path.exists(device_path):
        print(f"[INFO] Device '{device_name}' not found, skipping delete")
        return

    keys = list(_load_raw(device_path).get("keys", {}).keys())
    _clear_device_discovery(client, device_name, keys)
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

    old_path = os.path.join(DATA_DIR, f"{old_name}.json")
    new_path = os.path.join(DATA_DIR, f"{new_name}.json")

    if not os.path.exists(old_path):
        print(f"[INFO] Device '{old_name}' not found, skipping rename")
        return

    if os.path.exists(new_path):
        print(f"[INFO] Device '{new_name}' already exists, skipping rename")
        return

    keys = list(_load_raw(old_path).get("keys", {}).keys())
    _clear_device_discovery(client, old_name, keys)
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

    _startup["done"] = False

    # Subscribe to DEVICES_TOPIC to receive the retained previous device list.
    # on_message will call _do_startup once it arrives.
    # The timer fires if there is no retained message (first-ever install).
    client.subscribe(DEVICES_TOPIC)
    t = threading.Timer(1.0, _do_startup, args=[client, {}])
    t.daemon = True
    t.start()
    _startup["timer"] = t


def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode().strip()

    # First message on DEVICES_TOPIC is the retained previous session state.
    # Use it to clean up any stale discovery topics before publishing current state.
    if not _startup["done"] and topic == DEVICES_TOPIC:
        try:
            prev_devices = json.loads(payload) if payload else {}
        except Exception:
            prev_devices = {}
        _do_startup(client, prev_devices)
        return

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

    elif topic == KEY_SET_OPTIONS_TOPIC:
        _handle_set_options(client, payload)

    elif topic == VIRTUAL_KEY_CREATE_TOPIC:
        _handle_create_virtual_key(client, payload)

    elif topic == VIRTUAL_KEY_DELETE_TOPIC:
        _handle_delete_virtual_key(client, payload)

    else:
        parts = topic.split("/")
        if len(parts) == 3:
            device_name = parts[1]
            remote = _remotes.get(device_name)
            if remote is None:
                print(f"[ERROR] No cached remote for '{device_name}'")
                return
            try:
                vkey = _virtual_keys.get(device_name, {}).get(payload)
                if vkey:
                    base_key = vkey["key"]
                    repeat = vkey.get("repeat", 1)
                    delay_ms = vkey.get("delay_ms", 0)
                else:
                    base_key = payload
                    opts = _key_options.get(device_name, {}).get(payload, {})
                    repeat = opts.get("repeat", 1)
                    delay_ms = opts.get("delay_ms", 0)
                remote.send(base_key)
                for _ in range(repeat - 1):
                    time.sleep(delay_ms / 1000)
                    remote.send(base_key)
                log_suffix = (f" → {base_key}" if vkey else "") + (f" x{repeat}" if repeat > 1 else "")
                print(f"[INFO] Sent: {device_name}/{payload}{log_suffix}")
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
