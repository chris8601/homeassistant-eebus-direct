"""EEBUS Direct integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import EebusDataUpdateCoordinator
from .runtime import EebusRuntime

EebusConfigEntry = ConfigEntry


async def _async_reload_entry(hass: HomeAssistant, entry: EebusConfigEntry) -> None:
    """Reload an EEBUS entry after its UI options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: EebusConfigEntry) -> bool:
    """Set up one directly connected EEBUS peer."""
    runtime = EebusRuntime(hass, entry)
    coordinator = EebusDataUpdateCoordinator(hass, entry, runtime)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EebusConfigEntry) -> bool:
    """Unload the entry and close the SHIP/TLS session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.runtime.async_close()
    return unloaded
