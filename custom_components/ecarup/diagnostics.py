"""Diagnostics support for the eCarUp integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.redact import async_redact_data

from . import ECarUpConfigEntry

TO_REDACT = {
    "client_id",
    "client_secret",
    "driverIdentifier",
    "ownerId",
    "latitude",
    "longitude",
    "email",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ECarUpConfigEntry
) -> dict[str, Any]:
    """Return integration diagnostics without credentials or user identifiers."""
    entity_registry = async_get_entity_registry(hass)
    device_registry = async_get_device_registry(hass)
    entry_devices = {
        device.id
        for device in device_registry.devices.values()
        if entry.entry_id in device.config_entries
    }
    return async_redact_data(
        {
            "entry_data": entry.data,
            "coordinator_data": _diagnostic_station_data(
                entry.runtime_data.coordinator.data
            ),
            "api_authenticated": entry.runtime_data.api.authenticated,
            "devices": [
                {
                    "name": device.name,
                    "manufacturer": device.manufacturer,
                    "model": device.model,
                    "identifiers": device.identifiers,
                }
                for device in device_registry.devices.values()
                if device.id in entry_devices
            ],
            "entities": [
                {
                    "entity_id": entity.entity_id,
                    "disabled_by": entity.disabled_by,
                    "original_name": entity.original_name,
                }
                for entity in entity_registry.entities.values()
                if entity.config_entry_id == entry.entry_id
            ],
        },
        TO_REDACT,
    )


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ECarUpConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics scoped to one station device."""
    station_ids = {
        identifier
        for domain, identifier in device.identifiers
        if domain == "ecarup"
    }
    coordinator_data = entry.runtime_data.coordinator.data
    return async_redact_data(
        {
            "station_data": _diagnostic_station_data(
                {
                    station_id: coordinator_data[station_id]
                    for station_id in station_ids
                    if station_id in coordinator_data
                }
            )
        },
        TO_REDACT,
    )


def _diagnostic_station_data(stations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Include useful state while omitting driver history and station locations."""
    return {
        station_id: {
            "station": {
                key: value
                for key, value in data["station"].items()
                if key not in {"latitude", "longitude"}
            },
            "active": data["active"],
            "history_count": len(data["history"]),
            "history_has_more": data["history_has_more"],
            "history_backfill_complete": data["history_backfill_complete"],
            "active_unavailable": list(data.get("active_unavailable", set())),
        }
        for station_id, data in stations.items()
    }
