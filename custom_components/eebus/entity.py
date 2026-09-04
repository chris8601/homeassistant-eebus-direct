"""Base entity for EEBUS Direct."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EebusDataUpdateCoordinator


def get_path(data: dict[str, Any], *path: str) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


class EebusEntity(CoordinatorEntity[EebusDataUpdateCoordinator]):
    """Common device identity and availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EebusDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.runtime.peer_ski}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        peer = self.coordinator.data.get("peer", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.runtime.peer_ski)},
            manufacturer=peer.get("manufacturer") or "Hager",
            model=peer.get("model") or "Witty Flow",
            name=peer.get("name") or "EEBUS Wallbox",
            serial_number=peer.get("serial"),
            configuration_url=(f"http://{peer['host']}" if peer.get("host") else None),
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data.get("connected"))
