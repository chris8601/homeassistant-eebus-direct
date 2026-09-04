"""Constants for EEBUS Direct."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "eebus"
PLATFORMS: Final = ["binary_sensor", "button", "number", "sensor", "switch"]

CONF_INTERFACE_IP: Final = "interface_ip"
CONF_SCAN_SECONDS: Final = "scan_seconds"
CONF_SERVICE: Final = "service"
CONF_PEER_SKI: Final = "peer_ski"
CONF_IDENTITY_PATH: Final = "identity_path"
CONF_UPDATE_INTERVAL: Final = "update_interval"

DEFAULT_SCAN_SECONDS: Final = 4
DEFAULT_UPDATE_INTERVAL: Final = 10
PAIRING_WAIT_SECONDS: Final = 180
IDENTITY_DIRECTORY: Final = ".storage/eebus_direct"

ATTRIBUTION: Final = "Data provided locally by EEBUS"
