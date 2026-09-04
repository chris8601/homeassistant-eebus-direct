"""Sensors exposed by EEBUS EVSE/EV features."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfLength,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EebusDataUpdateCoordinator
from .entity import EebusEntity, get_path


@dataclass(frozen=True, kw_only=True)
class EebusSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[EebusSensorDescription, ...] = (
    EebusSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "ready", "charging", "fault"],
        value_fn=lambda data: data.get("status"),
    ),
    EebusSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: get_path(data, "measurements", "power_w"),
    ),
    EebusSensorDescription(
        key="session_energy",
        translation_key="session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
        value_fn=lambda data: get_path(data, "measurements", "session_energy_kwh"),
    ),
    EebusSensorDescription(
        key="total_energy",
        translation_key="total_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: get_path(data, "measurements", "total_energy_kwh"),
    ),
    EebusSensorDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: get_path(data, "measurements", "current_a"),
    ),
    *tuple(
        EebusSensorDescription(
            key=f"current_{phase}",
            translation_key=f"current_{phase}",
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            value_fn=lambda data, phase=phase: get_path(
                data, "measurements", f"current_{phase}_a"
            ),
        )
        for phase in ("a", "b", "c")
    ),
    *tuple(
        EebusSensorDescription(
            key=f"voltage_{phase}",
            translation_key=f"voltage_{phase}",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            value_fn=lambda data, phase=phase: get_path(
                data, "measurements", f"voltage_{phase}_v"
            ),
        )
        for phase in ("a", "b", "c")
    ),
    EebusSensorDescription(
        key="frequency",
        translation_key="frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: get_path(data, "measurements", "frequency_hz"),
    ),
    EebusSensorDescription(
        key="soc",
        translation_key="soc",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: get_path(data, "vehicle", "state_of_charge_pct"),
    ),
    EebusSensorDescription(
        key="soh",
        translation_key="soh",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: get_path(data, "vehicle", "state_of_health_pct"),
    ),
    EebusSensorDescription(
        key="range",
        translation_key="range",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: get_path(data, "vehicle", "range_km"),
    ),
    EebusSensorDescription(
        key="applied_current",
        translation_key="applied_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda data: get_path(data, "charging", "applied_current_a"),
    ),
    EebusSensorDescription(
        key="operating_state",
        translation_key="operating_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: get_path(data, "diagnostics", "operating_state"),
    ),
    EebusSensorDescription(
        key="use_cases",
        translation_key="use_cases",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: len(get_path(data, "diagnostics", "use_cases") or {}),
        attributes_fn=lambda data: get_path(data, "diagnostics", "use_cases") or {},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EebusDataUpdateCoordinator = entry.runtime_data
    async_add_entities(EebusSensor(coordinator, description) for description in SENSORS)


class EebusSensor(EebusEntity, SensorEntity):
    entity_description: EebusSensorDescription

    def __init__(
        self,
        coordinator: EebusDataUpdateCoordinator,
        description: EebusSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
