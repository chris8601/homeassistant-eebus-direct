"""EEBUS charging and solar-recommendation switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EebusDataUpdateCoordinator
from .entity import EebusEntity, get_path
from .runtime import EebusRuntimeError


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EebusDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        [EebusChargingSwitch(coordinator), EebusSolarSwitch(coordinator)]
    )


class EebusChargingSwitch(EebusEntity, SwitchEntity):
    """Pause/resume charging using the OPEV obligation limit."""

    _attr_translation_key = "charging_enabled"

    def __init__(self, coordinator: EebusDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "charging_enabled")

    @property
    def is_on(self) -> bool:
        return bool(get_path(self.coordinator.data, "charging", "enabled"))

    @property
    def available(self) -> bool:
        return super().available and bool(
            get_path(self.coordinator.data, "capabilities", "pause")
        )

    async def _set(self, enabled: bool) -> None:
        try:
            await self.coordinator.runtime.async_set_charging(enabled)
        except EebusRuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class EebusSolarSwitch(EebusEntity, SwitchEntity):
    """Activate/deactivate the OSCEV self-consumption recommendation."""

    _attr_translation_key = "solar_recommendation"

    def __init__(self, coordinator: EebusDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "solar_recommendation")

    @property
    def is_on(self) -> bool:
        return bool(get_path(self.coordinator.data, "charging", "solar_active"))

    @property
    def available(self) -> bool:
        return super().available and bool(
            get_path(self.coordinator.data, "capabilities", "solar_recommendation")
        )

    async def _set(self, enabled: bool) -> None:
        value = float(
            get_path(self.coordinator.data, "charging", "solar_current_a") or 6
        )
        try:
            await self.coordinator.runtime.async_set_solar_current(
                value, active=enabled
            )
        except EebusRuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
