"""Native SHIP identity handling for Home Assistant."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ._vendor.eebus_sdk.identity import (
    IdentityMaterial,
    build_qr_payload,
)


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
    return identity


def create_or_load_identity(config_dir: str) -> tuple[IdentityMaterial, str]:
    """Create one stable EEBUS CEM identity for this HA installation."""
    directory = Path(config_dir).joinpath(".storage", "eebus_direct").resolve()
    identity_path = directory / "identity.json"
    if identity_path.exists():
        return _load_identity(identity_path), str(identity_path)

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
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

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
    identity_path.write_text(
        json.dumps(identity.as_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    identity_path.chmod(0o600)
    return identity, str(identity_path)


def load_identity(path: str) -> IdentityMaterial:
    """Load identity material stored by :func:`create_or_load_identity`."""
    return _load_identity(Path(path).resolve())
