"""Writable EEBUS charging-current numbers."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
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
        [
            EebusCurrentNumber(coordinator),
            EebusPhaseCurrentNumber(coordinator, "a"),
            EebusPhaseCurrentNumber(coordinator, "b"),
            EebusPhaseCurrentNumber(coordinator, "c"),
            EebusSolarCurrentNumber(coordinator),
        ]
    )


class _EebusCurrentBase(EebusEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 0.1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_min_value(self) -> float:
        return max(
            0.0,
            float(get_path(self.coordinator.data, "charging", "min_current_a") or 6),
        )

    @property
    def native_max_value(self) -> float:
        return min(
            63.0,
            float(get_path(self.coordinator.data, "charging", "max_current_a") or 32),
        )

    async def _refresh(self) -> None:
        await self.coordinator.async_request_refresh()


class EebusCurrentNumber(_EebusCurrentBase):
    """Symmetric OPEV current ceiling for all active phases."""

    _attr_translation_key = "current_limit"

    def __init__(self, coordinator: EebusDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "current_limit")

    @property
    def native_value(self) -> float:
        return float(
            get_path(self.coordinator.data, "charging", "target_current_a")
            or get_path(self.coordinator.data, "charging", "applied_current_a")
            or self.native_min_value
        )

    @property
    def available(self) -> bool:
        return super().available and bool(
            get_path(self.coordinator.data, "capabilities", "current_limit")
        )

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.runtime.async_set_current(value)
        except EebusRuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self._refresh()


class EebusPhaseCurrentNumber(_EebusCurrentBase):
    """Asymmetric OPEV limit for one phase when the EV advertises it."""

    def __init__(self, coordinator: EebusDataUpdateCoordinator, phase: str) -> None:
        super().__init__(coordinator, f"current_limit_{phase}")
        self.phase = phase
        self._attr_translation_key = f"current_limit_{phase}"

    @property
    def native_value(self) -> float:
        target = get_path(self.coordinator.data, "charging", "target_phase_a") or {}
        limit = (
            get_path(
                self.coordinator.data,
                "diagnostics",
                "limits",
                "obligation",
                "limits",
                self.phase,
            )
            or {}
        )
        return float(
            target.get(self.phase) or limit.get("value_a") or self.native_min_value
        )

    @property
    def available(self) -> bool:
        limits = (
            get_path(
                self.coordinator.data, "diagnostics", "limits", "obligation", "limits"
            )
            or {}
        )
        return super().available and self.phase in limits and len(limits) > 1

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.runtime.async_set_current(value, self.phase)
        except EebusRuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self._refresh()


class EebusSolarCurrentNumber(_EebusCurrentBase):
    """OSCEV recommendation for available self-produced current."""

    _attr_translation_key = "solar_current"

    def __init__(self, coordinator: EebusDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "solar_current")

    @property
    def native_value(self) -> float:
        return float(
            get_path(self.coordinator.data, "charging", "solar_current_a")
            or self.native_min_value
        )

    @property
    def available(self) -> bool:
        return super().available and bool(
            get_path(self.coordinator.data, "capabilities", "solar_recommendation")
        )

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.runtime.async_set_solar_current(value)
        except EebusRuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self._refresh()
