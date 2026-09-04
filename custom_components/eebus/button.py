"""Maintenance buttons for EEBUS Direct."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EebusDataUpdateCoordinator
from .entity import EebusEntity
from .runtime import EebusRuntimeError


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EebusDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        [EebusReleaseButton(coordinator), EebusReconnectButton(coordinator)]
    )


class EebusReleaseButton(EebusEntity, ButtonEntity):
    """Release every Home Assistant OPEV/OSCEV limit."""

    _attr_translation_key = "release_limits"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EebusDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "release_limits")

    @property
    def available(self) -> bool:
        capabilities = self.coordinator.data.get("capabilities", {})
        return super().available and bool(
            capabilities.get("current_limit")
            or capabilities.get("solar_recommendation")
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.runtime.async_release_limits()
        except EebusRuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()


class EebusReconnectButton(EebusEntity, ButtonEntity):
    """Force mDNS rediscovery and a new SHIP session."""

    _attr_translation_key = "reconnect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EebusDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "reconnect")

    async def async_press(self) -> None:
        try:
            await self.coordinator.runtime.async_reconnect()
        except EebusRuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()
