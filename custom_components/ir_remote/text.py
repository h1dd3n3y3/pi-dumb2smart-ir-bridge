import json
import logging

from homeassistant.components import mqtt

_LOGGER = logging.getLogger(__name__)
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    prefix = entry.data.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)
    added = set()
    entry_data = hass.data[DOMAIN][entry.entry_id]

    new_device_entity = NewDeviceNameText(prefix)
    entry_data["new_device_name_text"] = new_device_entity
    rename_device_entity = RenameTargetDeviceText(prefix)
    entry_data["rename_device_name_text"] = rename_device_entity
    async_add_entities([new_device_entity, rename_device_entity])

    @callback
    def handle_devices(msg):
        try:
            devices = json.loads(msg.payload)
        except Exception:
            _LOGGER.exception("Failed to parse devices payload: %s", msg.payload)
            return

        _LOGGER.debug("handle_devices fired: %s", list(devices.keys()))

        valid_unique_ids: set[str] = {
            f"ir_remote_{prefix}_new_remote",
            f"ir_remote_{prefix}_rename_remote_to",
        }
        for device_name in devices:
            valid_unique_ids.add(f"ir_remote_{prefix}_{device_name}_key_name")
            valid_unique_ids.add(f"ir_remote_{prefix}_{device_name}_rename_to")

        registry = er.async_get(hass)
        for entry_item in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entry_item.domain == "text" and entry_item.unique_id not in valid_unique_ids:
                registry.async_remove(entry_item.entity_id)

        new_entities = []
        for device_name in devices:
            if device_name not in added:
                added.add(device_name)
                key_entity = KeyNameText(prefix, device_name)
                entry_data["key_name_texts"][device_name] = key_entity
                new_entities.append(key_entity)
                rename_entity = RenameTargetText(prefix, device_name)
                entry_data["rename_target_texts"][device_name] = rename_entity
                new_entities.append(rename_entity)

        if new_entities:
            _LOGGER.debug("Adding %d text entities for: %s", len(new_entities), [e.unique_id for e in new_entities])
            async_add_entities(new_entities)

    await mqtt.async_subscribe(hass, f"{prefix}/devices", handle_devices)


class NewDeviceNameText(TextEntity):
    def __init__(self, prefix: str) -> None:
        self._attr_name = "Device Name"
        self._attr_unique_id = f"ir_remote_{prefix}_new_remote"
        self._attr_native_value = ""
        self._attr_native_min = 0
        self._attr_native_max = 64
        self._attr_available = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:remote-tv"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ir_{prefix}_bridge")},
            name="IR Bridge",
            model="ANAVI IR pHAT",
            manufacturer="ANAVI",
        )

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def clear(self) -> None:
        self._attr_native_value = ""
        self.async_write_ha_state()


class KeyNameText(TextEntity):
    def __init__(self, prefix: str, device_name: str) -> None:
        self._attr_name = f"{device_name.replace('_', ' ').title()} Key Name"
        self._attr_unique_id = f"ir_remote_{prefix}_{device_name}_key_name"
        self._attr_native_value = ""
        self._attr_native_min = 0
        self._attr_native_max = 64
        self._attr_available = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:alpha-a-box-outline"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ir_{prefix}_{device_name}")},
            name=device_name.replace("_", " ").title(),
            model="ANAVI IR pHAT",
            manufacturer="ANAVI",
        )

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def clear(self) -> None:
        self._attr_native_value = ""
        self.async_write_ha_state()


class RenameTargetDeviceText(TextEntity):
    def __init__(self, prefix: str) -> None:
        self._attr_name = "Remote New Name"
        self._attr_unique_id = f"ir_remote_{prefix}_rename_remote_to"
        self._attr_native_value = ""
        self._attr_native_min = 0
        self._attr_native_max = 64
        self._attr_available = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:form-textbox"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ir_{prefix}_bridge")},
            name="IR Bridge",
            model="ANAVI IR pHAT",
            manufacturer="ANAVI",
        )

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def clear(self) -> None:
        self._attr_native_value = ""
        self.async_write_ha_state()


class RenameTargetText(TextEntity):
    def __init__(self, prefix: str, device_name: str) -> None:
        self._attr_name = f"{device_name.replace('_', ' ').title()} New Key Name"
        self._attr_unique_id = f"ir_remote_{prefix}_{device_name}_rename_to"
        self._attr_native_value = ""
        self._attr_native_min = 0
        self._attr_native_max = 64
        self._attr_available = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:form-textbox"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ir_{prefix}_{device_name}")},
            name=device_name.replace("_", " ").title(),
            model="ANAVI IR pHAT",
            manufacturer="ANAVI",
        )

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def clear(self) -> None:
        self._attr_native_value = ""
        self.async_write_ha_state()
