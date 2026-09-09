"""Binary sensors for EEBUS charging state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EebusDataUpdateCoordinator
from .entity import EebusEntity, get_path


@dataclass(frozen=True, kw_only=True)
class EebusBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS = (
    EebusBinaryDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: bool(data.get("connected")),
    ),
    EebusBinaryDescription(
        key="vehicle_connected",
        translation_key="vehicle_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda data: bool(get_path(data, "charging", "vehicle_connected")),
    ),
    EebusBinaryDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: get_path(data, "charging", "active"),
    ),
    EebusBinaryDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: bool(get_path(data, "diagnostics", "fault")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EebusDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        EebusBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class EebusBinarySensor(EebusEntity, BinarySensorEntity):
    entity_description: EebusBinaryDescription

    def __init__(
        self,
        coordinator: EebusDataUpdateCoordinator,
        description: EebusBinaryDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None
