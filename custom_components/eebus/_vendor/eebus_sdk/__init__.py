"""Embedded EEBUS SHIP/SPINE protocol stack."""

from .client import HemsClient
from .discovery import ShipService, detect_interface_ip, discover_ship_services
from .exceptions import (
    CertificateMismatchError,
    DiscoveryError,
    EebusError,
    IdentityError,
    PairingRejectedError,
    ShipError,
    ShipHandshakeError,
    TransportError,
    TrustError,
    WebSocketProtocolError,
)
from .identity import IdentityMaterial
from .trust import CertificatePins, TrustStore

__all__ = [
    "CertificateMismatchError",
    "CertificatePins",
    "DiscoveryError",
    "EebusError",
    "HemsClient",
    "IdentityError",
    "IdentityMaterial",
    "PairingRejectedError",
    "ShipError",
    "ShipHandshakeError",
    "ShipService",
    "TransportError",
    "TrustError",
    "TrustStore",
    "WebSocketProtocolError",
    "detect_interface_ip",
    "discover_ship_services",
]

__version__ = "0.1.0a0+ha1"
