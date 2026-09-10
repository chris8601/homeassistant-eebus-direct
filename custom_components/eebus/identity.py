"""Native SHIP identity handling for Home Assistant."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ._vendor.eebus_sdk.identity import (
    IdentityMaterial,
    build_qr_payload,
)

_LOGGER = logging.getLogger(__name__)
_IDENTITY_LOCK = Lock()


def normalize_peer_ski(value: str) -> str:
    """Normalize and validate the 20-byte EEBUS SKI entered by the user."""
    normalized = re.sub(r"[\s:-]", "", value).lower()
    if re.fullmatch(r"[0-9a-f]{40}", normalized) is None:
        raise ValueError("EEBUS SKI must contain exactly 40 hexadecimal characters")
    return normalized


def _load_identity(path: Path) -> IdentityMaterial:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("cert_path", "key_path"):
        candidate = Path(data[key])
        if not candidate.is_absolute():
            data[key] = str((path.parent / candidate).resolve())
    identity = IdentityMaterial(**data)
    if not Path(identity.cert_path).is_file() or not Path(identity.key_path).is_file():
        raise FileNotFoundError("EEBUS identity certificate or key is missing")
    certificate = x509.load_pem_x509_certificate(
        Path(identity.cert_path).read_bytes()
    )
    private_key = serialization.load_pem_private_key(
        Path(identity.key_path).read_bytes(), password=None
    )
    certificate_public_key = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if certificate_public_key != private_public_key:
        raise ValueError("EEBUS certificate and private key do not match")
    certificate_ski = certificate.extensions.get_extension_for_class(
        x509.SubjectKeyIdentifier
    ).value.digest.hex()
    if identity.ski.lower() != certificate_ski:
        raise ValueError("Stored EEBUS SKI does not match the certificate")
    return identity


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    """Write one identity file without exposing a partially written version."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _create_identity(directory: Path, identity_path: Path) -> IdentityMaterial:
    """Generate and atomically store one matching certificate/key pair."""
    directory.mkdir(parents=True, exist_ok=True)
    device_id = f"HA-{uuid4().hex[:16].upper()}"
    ship_id = f"i:HA_u:{device_id}_r:CEM"
    common_name = f"{device_id}.cls"

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "DE"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Home Assistant"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "EEBUS CEM"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    ski = certificate.extensions.get_extension_for_class(
        x509.SubjectKeyIdentifier
    ).value.digest.hex()

    key_path = directory / "client.key.pem"
    cert_path = directory / "client.crt.pem"
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    certificate_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    _atomic_write(key_path, key_bytes)
    _atomic_write(cert_path, certificate_bytes)

    identity = IdentityMaterial(
        ship_id=ship_id,
        device_id=device_id,
        common_name=common_name,
        ski=ski,
        cert_path=str(cert_path),
        key_path=str(key_path),
        qr_payload=build_qr_payload(
            ship_id,
            ski,
            brand="Home Assistant",
            model="EEBUS Direct",
            device_type="EnergyManagementSystem",
        ),
    )
    _atomic_write(
        identity_path,
        json.dumps(identity.as_dict(), indent=2, sort_keys=True).encode("utf-8"),
    )
    return identity


def _load_or_repair_identity(identity_path: Path) -> IdentityMaterial:
    if identity_path.exists():
        try:
            return _load_identity(identity_path)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            x509.ExtensionNotFound,
        ) as exc:
            _LOGGER.warning(
                "Stored EEBUS identity is invalid and will be regenerated: %s", exc
            )
    return _create_identity(identity_path.parent, identity_path)


def create_or_load_identity(config_dir: str) -> tuple[IdentityMaterial, str]:
    """Create one stable, validated EEBUS CEM identity for this HA installation."""
    directory = Path(config_dir).joinpath(".storage", "eebus_direct").resolve()
    identity_path = directory / "identity.json"
    with _IDENTITY_LOCK:
        identity = _load_or_repair_identity(identity_path)
    return identity, str(identity_path)


def load_identity(path: str) -> IdentityMaterial:
    """Load and, if necessary, repair stored identity material."""
    identity_path = Path(path).resolve()
    with _IDENTITY_LOCK:
        return _load_or_repair_identity(identity_path)
