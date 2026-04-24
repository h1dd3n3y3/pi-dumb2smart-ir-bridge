import json

import voluptuous as vol
from homeassistant.components import mqtt, persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX, DOMAIN

PLATFORMS = ["button", "sensor", "text"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "key_name_texts": {},
        "rename_target_texts": {},
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    prefix = entry.data.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)

    async def _handle_restart_needed(msg):
        persistent_notification.async_create(
            hass,
            "The IR Remote integration has been updated. "
            "[Restart Home Assistant](/config/system) to apply the changes.",
            title="IR Remote: Restart Required",
            notification_id="ir_remote_restart_required",
        )

    await mqtt.async_subscribe(hass, f"{prefix}/system/restart_needed", _handle_restart_needed)

    if not hass.services.has_service(DOMAIN, "record_key"):

        async def record_key(call: ServiceCall) -> None:
            await mqtt.async_publish(
                hass,
                f"{prefix}/record/start",
                json.dumps({"device": call.data["device"], "key": call.data["key"]}),
            )

        async def delete_key(call: ServiceCall) -> None:
            await mqtt.async_publish(
                hass,
                f"{prefix}/key/delete",
                json.dumps({"device": call.data["device"], "key": call.data["key"]}),
            )

        async def rename_key(call: ServiceCall) -> None:
            await mqtt.async_publish(
                hass,
                f"{prefix}/key/rename",
                json.dumps({
                    "device": call.data["device"],
                    "old": call.data["old_key"],
                    "new": call.data["new_key"],
                }),
            )

        schema_device_key = vol.Schema({
            vol.Required("device"): cv.string,
            vol.Required("key"): cv.string,
        })

        hass.services.async_register(DOMAIN, "record_key", record_key, schema=schema_device_key)
        hass.services.async_register(DOMAIN, "delete_key", delete_key, schema=schema_device_key)
        hass.services.async_register(
            DOMAIN, "rename_key", rename_key,
            schema=vol.Schema({
                vol.Required("device"): cv.string,
                vol.Required("old_key"): cv.string,
                vol.Required("new_key"): cv.string,
            }),
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "record_key")
            hass.services.async_remove(DOMAIN, "delete_key")
            hass.services.async_remove(DOMAIN, "rename_key")
        return True
    return False
