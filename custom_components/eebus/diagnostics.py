"""Diagnostics for EEBUS Direct."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return protocol capabilities without certificate/private-key material."""
    coordinator = entry.runtime_data
    state = coordinator.data
    return {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "interface_ip": entry.options.get(
                "interface_ip", entry.data.get("interface_ip")
            ),
        },
        "peer": state.get("peer"),
        "capabilities": state.get("capabilities"),
        "charging": state.get("charging"),
        "measurements": state.get("measurements"),
        "vehicle": state.get("vehicle"),
        "diagnostics": state.get("diagnostics"),
    }
