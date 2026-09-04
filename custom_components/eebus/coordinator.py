"""Coordinator for the direct EEBUS connection."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN
from .runtime import EebusRuntime, EebusRuntimeError

_LOGGER = logging.getLogger(__name__)


class EebusDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll static/dynamic SPINE data while accepting EEBUS notifications."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: EebusRuntime,
    ) -> None:
        interval = int(entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.unique_id or entry.entry_id}",
            update_interval=timedelta(seconds=max(5, interval)),
            config_entry=entry,
        )
        self.runtime = runtime

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.runtime.async_update()
        except EebusRuntimeError as exc:
            raise UpdateFailed(str(exc)) from exc
