"""Tests for the embedded certificate and SPINE command implementation."""

from __future__ import annotations

import importlib
import json
import ssl
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "eebus"

# Import component submodules without executing Home Assistant's integration loader.
custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)
eebus = types.ModuleType("custom_components.eebus")
eebus.__path__ = [str(COMPONENT)]
sys.modules.setdefault("custom_components.eebus", eebus)

identity_module = importlib.import_module("custom_components.eebus.identity")
sdk_identity = importlib.import_module(
    "custom_components.eebus._vendor.eebus_sdk.identity"
)
json_codec = importlib.import_module(
    "custom_components.eebus._vendor.eebus_sdk.json_codec"
)
spine = importlib.import_module("custom_components.eebus._vendor.eebus_sdk.spine")
client_module = importlib.import_module(
    "custom_components.eebus._vendor.eebus_sdk.client"
)
discovery_module = importlib.import_module(
    "custom_components.eebus._vendor.eebus_sdk.discovery"
)
trust_module = importlib.import_module(
    "custom_components.eebus._vendor.eebus_sdk.trust"
)
ship_module = importlib.import_module("custom_components.eebus._vendor.eebus_sdk.ship")
websocket_module = importlib.import_module(
    "custom_components.eebus._vendor.eebus_sdk.websocket"
)


class _AccessHandshakeTransport:
    """Deterministic SHIP transport for the access-methods exchange."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.frames = [
            self._control_frame({"accessMethodsRequest": {}}),
            self._control_frame(
                {"accessMethods": {"id": "i:HAGER_u:WITTY_r:EVSE"}}
            ),
        ]

    @staticmethod
    def _control_frame(payload: dict[str, object]):
        encoded = bytes([ship_module.SHIP_MSG_CONTROL]) + json_codec.to_eebus_json_bytes(
            payload
        )
        return websocket_module.WebSocketFrame(opcode=0x2, payload=encoded)

    async def send_binary(self, payload: bytes) -> None:
        self.sent.append(payload)

    async def receive_frame(self):
        return self.frames.pop(0)


class ProtocolTests(unittest.TestCase):
    def test_wallbox_ski_input_is_normalized_and_validated(self) -> None:
        entered = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD"
        self.assertEqual(
            identity_module.normalize_peer_ski(entered),
            "aabbccddeeff00112233445566778899aabbccdd",
        )
        with self.assertRaises(ValueError):
            identity_module.normalize_peer_ski("AA:BB:CC")
        with self.assertRaises(ValueError):
            identity_module.normalize_peer_ski("z" * 40)

    def test_native_identity_round_trip_and_peer_ski(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first, path = identity_module.create_or_load_identity(directory)
            second = identity_module.load_identity(path)
            self.assertEqual(first.ski, second.ski)
            self.assertEqual(len(first.ski), 40)

            from cryptography import x509
            from cryptography.hazmat.primitives import serialization

            certificate = x509.load_pem_x509_certificate(
                Path(first.cert_path).read_bytes()
            )
            der = certificate.public_bytes(serialization.Encoding.DER)
            self.assertEqual(sdk_identity.extract_ski_from_peer_cert(der), first.ski)
            self.assertEqual(Path(first.key_path).stat().st_mode & 0o777, 0o600)

    def test_mismatched_certificate_and_key_are_repaired(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as other_directory,
        ):
            first, path = identity_module.create_or_load_identity(directory)
            other, _ = identity_module.create_or_load_identity(other_directory)
            Path(first.key_path).write_bytes(Path(other.key_path).read_bytes())

            repaired, repaired_path = identity_module.create_or_load_identity(directory)

            self.assertEqual(path, repaired_path)
            self.assertNotEqual(first.ski, repaired.ski)
            context = ssl.create_default_context()
            context.load_cert_chain(repaired.cert_path, repaired.key_path)

    def test_parallel_identity_creation_returns_one_valid_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda _: identity_module.create_or_load_identity(directory),
                        range(24),
                    )
                )

            identities = [identity for identity, _ in results]
            self.assertEqual(len({identity.ski for identity in identities}), 1)
            context = ssl.create_default_context()
            context.load_cert_chain(
                identities[0].cert_path,
                identities[0].key_path,
            )

    def test_outbound_spine_commands_include_function(self) -> None:
        datagram = spine.build_read_datagram(
            source={"device": "LOCAL", "entity": [1], "feature": 6},
            destination={"device": "REMOTE", "entity": [1, 1], "feature": 11},
            msg_counter=1,
            function_name="measurementListData",
        )
        command = spine.extract_commands(datagram)[0]
        self.assertEqual(command["function"], "measurementListData")
        wire = json.loads(
            json_codec.to_eebus_json_bytes(datagram.as_ship_payload()).decode()
        )
        self.assertIn("function", json.dumps(wire))

    def test_partial_read_uses_spine_filter_and_empty_function_data(self) -> None:
        datagram = spine.build_read_datagram(
            source={"device": "LOCAL", "entity": [1], "feature": 6},
            destination={"device": "REMOTE", "entity": [1, 1], "feature": 11},
            msg_counter=2,
            function_name="measurementListData",
            partial=True,
        )
        command = spine.extract_commands(datagram)[0]
        self.assertEqual(command["measurementListData"], [])
        self.assertEqual(
            command["filter"],
            {
                "cmdControl": {"partial": {}},
                "measurementListDataSelectors": {},
            },
        )
        decoded_wire = json_codec.from_eebus_json_bytes(
            json_codec.to_eebus_json_bytes(datagram.as_ship_payload())
        )
        wire_command = decoded_wire["data"]["payload"]["datagram"]["payload"]["cmd"][0]
        self.assertIn("partial", wire_command["filter"]["cmdControl"])
        self.assertIn("measurementListDataSelectors", wire_command["filter"])

    def test_full_read_has_no_filter(self) -> None:
        command = spine.extract_commands(
            spine.build_read_datagram(
                source={"device": "LOCAL", "entity": [1], "feature": 6},
                destination={"device": "REMOTE", "entity": [1, 1], "feature": 11},
                msg_counter=3,
                function_name="measurementListData",
            )
        )[0]
        self.assertNotIn("filter", command)


class ShipHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_access_exchange_uses_only_standard_ship_messages(self) -> None:
        transport = _AccessHandshakeTransport()
        identity = sdk_identity.IdentityMaterial(
            ship_id="i:HA_u:TEST_r:CEM",
            device_id="TEST",
            common_name="TEST.cls",
            ski="00" * 20,
            cert_path="/tmp/no-cert",
            key_path="/tmp/no-key",
            qr_payload="",
        )
        session = ship_module.ShipSession(
            ship_module.ShipConnectionConfig(
                host="192.0.2.10",
                port=4712,
                path="/ship/",
                server_name="witty.local",
            ),
            identity,
            trust_module.TrustStore(),
            transport=transport,
        )

        await session._access_methods_handshake()

        sent_messages = [
            json_codec.from_eebus_json_bytes(message[1:])
            for message in transport.sent
        ]
        self.assertEqual(
            sent_messages,
            [
                {"accessMethodsRequest": []},
                {"accessMethods": {"id": "i:HA_u:TEST_r:CEM"}},
            ],
        )
        self.assertFalse(
            any("accessMethodsResponse" in message for message in sent_messages)
        )
        self.assertEqual(session.remote_ship_id, "i:HAGER_u:WITTY_r:EVSE")

    def test_default_profile_is_an_ev_cem(self) -> None:
        material = sdk_identity.IdentityMaterial(
            ship_id="i:HA_u:TEST_r:CEM",
            device_id="TEST",
            common_name="TEST.cls",
            ski="00" * 20,
            cert_path="/tmp/no-cert",
            key_path="/tmp/no-key",
            qr_payload="",
        )
        client = client_module.HemsClient(
            session=object(),
            service=discovery_module.ShipService(service_name="wallbox", port=4712),
            identity=material,
            trust=trust_module.TrustStore(),
        )
        discovery = client.build_local_detailed_discovery()
        feature_types = {
            (item["description"]["featureType"], item["description"]["role"])
            for item in discovery["featureInformation"]
        }
        self.assertIn(("DeviceDiagnosis", "server"), feature_types)
        self.assertIn(("LoadControl", "client"), feature_types)
        self.assertIn(("Measurement", "client"), feature_types)
        use_cases = client._profile_use_case_data()
        names = {
            item["useCaseName"]
            for information in use_cases["useCaseInformation"]
            for item in information["useCaseSupport"]
        }
        self.assertIn("overloadProtectionByEvChargingCurrentCurtailment", names)
        self.assertIn("optimizationOfSelfConsumptionDuringEvCharging", names)
        self.assertIn("evStateOfCharge", names)


class _MemorySession:
    def __init__(self) -> None:
        self.sent = []

    async def send_spine(self, datagram):
        self.sent.append(datagram)


class _RejectedReadSession(_MemorySession):
    async def receive_datagram(self, *, timeout=None):
        return spine.build_result_datagram(
            self.sent[-1],
            source={"device": "REMOTE", "entity": [1, 1], "feature": 11},
            msg_counter=99,
            error_number=5,
            description="read rejected",
        )


class IncomingProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_read_surfaces_correlated_spine_error(self) -> None:
        session = _RejectedReadSession()
        material = sdk_identity.IdentityMaterial(
            ship_id="i:HA_u:TEST_r:CEM",
            device_id="TEST",
            common_name="TEST.cls",
            ski="00" * 20,
            cert_path="/tmp/no-cert",
            key_path="/tmp/no-key",
            qr_payload="",
        )
        client = client_module.HemsClient(
            session=session,
            service=discovery_module.ShipService(service_name="wallbox", port=4712),
            identity=material,
            trust=trust_module.TrustStore(),
        )

        with self.assertRaisesRegex(
            client_module.SpineResultError, "SPINE error 5: read rejected"
        ):
            await client._request_function_data(
                source={"device": "LOCAL", "entity": [1], "feature": 6},
                destination={"device": "REMOTE", "entity": [1, 1], "feature": 11},
                function_name="measurementListData",
                extractor=lambda datagram: [],
                timeout=0.1,
                partial=True,
            )

    async def test_real_command_shape_receives_use_case_reply(self) -> None:
        material = sdk_identity.IdentityMaterial(
            ship_id="i:HA_u:TEST_r:CEM",
            device_id="TEST",
            common_name="TEST.cls",
            ski="00" * 20,
            cert_path="/tmp/no-cert",
            key_path="/tmp/no-key",
            qr_payload="",
        )
        session = _MemorySession()
        client = client_module.HemsClient(
            session=session,
            service=discovery_module.ShipService(service_name="wallbox", port=4712),
            identity=material,
            trust=trust_module.TrustStore(),
        )
        request = spine.build_datagram(
            source={"device": "REMOTE", "entity": [0], "feature": 0},
            destination=client.local_node_management_address(),
            cmd_classifier="read",
            msg_counter=12,
            commands=[
                {
                    "function": "nodeManagementUseCaseData",
                    "nodeManagementUseCaseData": [],
                }
            ],
        )
        await client.handle_incoming_datagram(request)
        replies = [
            datagram
            for datagram in session.sent
            if spine.extract_header(datagram).get("cmdClassifier") == "reply"
        ]
        self.assertEqual(len(replies), 1)
        command = spine.extract_commands(replies[0])[0]
        self.assertEqual(command["function"], "nodeManagementUseCaseData")
        self.assertIn("nodeManagementUseCaseData", command)


if __name__ == "__main__":
    unittest.main()
