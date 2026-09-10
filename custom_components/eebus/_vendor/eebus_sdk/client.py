"""High-level HEMS client built on top of a SHIP session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from ._client_profile import ClientSpineProfile
from ._spine_helpers import feature_address_string, feature_addresses
from .discovery import ShipService, discover_ship_services
from .identity import IdentityMaterial
from .ship import ShipConnectionConfig, ShipEvent, ShipSession
from .spine import (
    SPECIFICATION_VERSION,
    SUPPORTED_SPECIFICATION_VERSIONS,
    SpineDatagram,
    build_datagram,
    build_read_datagram,
    build_reply_datagram,
    build_result_datagram,
    extract_commands,
    extract_discovery_payloads,
    extract_header,
    extract_measurement_descriptions,
    extract_measurement_payloads,
)
from .exceptions import SpineResultError
from .trace import TraceLogger
from .trust import TrustStore


@dataclass(slots=True)
class HemsClient:
    """High-level EEBus client built on top of an established SHIP session."""

    session: ShipSession
    service: ShipService
    identity: IdentityMaterial
    trust: TrustStore
    interface_ip: str | None = None
    profile: str = "default"
    _spine_msg_counter: int = field(default=1, init=False, repr=False)
    _remote_device_address: str | None = field(default=None, init=False, repr=False)
    _node_management_bootstrap_sent: bool = field(default=False, init=False, repr=False)
    _spine_profile: ClientSpineProfile = field(init=False, repr=False)
    _last_remote_discovery: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _remote_device_diagnosis_bootstrapped: set[str] = field(default_factory=set, init=False, repr=False)
    _initial_node_management_subscription_sent: bool = field(default=False, init=False, repr=False)
    _specification_version: str = field(
        default=SPECIFICATION_VERSION, init=False, repr=False
    )
    _peer_specification_versions: list[str] = field(
        default_factory=list, init=False, repr=False
    )
    _recent_traffic: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False
    )
    _remote_function_cache: dict[str, Any] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.profile not in {"default", "hems-reference", "cls-adapter"}:
            raise ValueError(f"unsupported client profile: {self.profile}")
        self._spine_profile = ClientSpineProfile(identity=self.identity, profile=self.profile)

    @classmethod
    async def connect(
        cls,
        service: ShipService,
        identity: IdentityMaterial,
        trust: TrustStore,
        *,
        interface_ip: str | None = None,
        trace_logger: TraceLogger | None = None,
        pairing_wait_seconds: int = 60,
        timeout: float = 10.0,
        profile: str = "default",
    ) -> "HemsClient":
        """Open a SHIP session to ``service`` and return a ready-to-use client."""
        if service.port is None:
            raise ValueError(f"{service.service_name} does not advertise a port")
        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=service.preferred_host(),
                port=service.port,
                path=service.path,
                server_name=service.server_name(),
                timeout=timeout,
                pairing_wait_seconds=pairing_wait_seconds,
            ),
            identity,
            trust,
            trace_logger=trace_logger,
        )
        return cls(
            session=session,
            service=service,
            identity=identity,
            trust=trust,
            interface_ip=interface_ip,
            profile=profile,
        )

    async def close(self) -> None:
        await self.session.close()

    async def reconnect(self, *, timeout: float | None = None) -> None:
        """Rediscover the current peer when possible and reopen the SHIP session."""
        await self.close()
        service = self.service
        if self.interface_ip is not None:
            services = await asyncio.to_thread(discover_ship_services, self.interface_ip, timeout=timeout or 3.0)
            for candidate in services:
                if candidate.service_name == self.service.service_name:
                    service = candidate
                    break
        refreshed = await self.connect(
            service,
            self.identity,
            self.trust,
            interface_ip=self.interface_ip,
            pairing_wait_seconds=self.session.config.pairing_wait_seconds,
            timeout=timeout or self.session.config.timeout,
            profile=self.profile,
        )
        self.session = refreshed.session
        self.service = refreshed.service

    def _uses_hems_reference_profile(self) -> bool:
        return self._spine_profile._uses_hems_reference_profile()

    def _uses_cls_adapter_profile(self) -> bool:
        return self._spine_profile._uses_cls_adapter_profile()

    def _uses_structured_server_profile(self) -> bool:
        return self._spine_profile._uses_structured_server_profile()

    def _hems_reference_local_device_address(self) -> str:
        return self._spine_profile._hems_reference_local_device_address()

    def local_device_address(self) -> str:
        return self._spine_profile.local_device_address()

    def local_node_management_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [0], "feature": 0}

    def local_measurement_client_address(self) -> dict[str, Any]:
        if self._uses_structured_server_profile():
            return {"device": self.local_device_address(), "entity": [1], "feature": 9}
        return {"device": self.local_device_address(), "entity": [1], "feature": 6}

    def local_electrical_connection_client_address(self) -> dict[str, Any]:
        if self._uses_structured_server_profile():
            return {"device": self.local_device_address(), "entity": [1], "feature": 8}
        return {"device": self.local_device_address(), "entity": [1], "feature": 5}

    def local_device_diagnosis_client_address(self) -> dict[str, Any]:
        if self._uses_structured_server_profile():
            return {"device": self.local_device_address(), "entity": [1], "feature": 1}
        return {"device": self.local_device_address(), "entity": [1], "feature": 2}

    def local_device_classification_client_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [1], "feature": 3}

    def local_device_configuration_client_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [1], "feature": 4}

    def local_load_control_client_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [1], "feature": 7}

    def local_identification_client_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [1], "feature": 8}

    def local_device_diagnosis_server_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [1], "feature": 1}

    def _next_msg_counter(self) -> int:
        value = self._spine_msg_counter
        self._spine_msg_counter += 1
        return value

    @property
    def specification_version(self) -> str:
        """Return the SPINE version currently selected for this peer."""
        return self._specification_version

    @property
    def peer_specification_versions(self) -> list[str]:
        """Return the SPINE versions announced by the peer."""
        return list(self._peer_specification_versions)

    @property
    def recent_traffic(self) -> list[dict[str, Any]]:
        """Return bounded, payload-free SPINE traffic metadata for diagnostics."""
        return [dict(item) for item in self._recent_traffic]

    @staticmethod
    def _command_name(command: dict[str, Any]) -> str:
        function_name = command.get("function")
        if isinstance(function_name, str):
            return function_name
        return next(
            (
                key
                for key in command
                if key not in {"function", "filter"}
            ),
            "unknown",
        )

    def _remember_traffic(self, direction: str, datagram: SpineDatagram) -> None:
        try:
            header = extract_header(datagram)
            commands = extract_commands(datagram)
        except (TypeError, ValueError):
            return
        summary: dict[str, Any] = {
            "direction": direction,
            "specification_version": header.get("specificationVersion"),
            "classifier": header.get("cmdClassifier"),
            "msg_counter": header.get("msgCounter"),
            "msg_counter_reference": header.get("msgCounterReference"),
            "ack_request": header.get("ackRequest"),
            "source": header.get("addressSource"),
            "destination": header.get("addressDestination"),
            "commands": [self._command_name(command) for command in commands],
        }
        results = [
            command["resultData"]
            for command in commands
            if isinstance(command.get("resultData"), dict)
        ]
        if results:
            summary["results"] = results
        self._recent_traffic.append(summary)
        del self._recent_traffic[:-100]

    async def _send_spine(self, datagram: SpineDatagram | dict[str, Any]) -> None:
        if isinstance(datagram, SpineDatagram):
            self._remember_traffic("tx", datagram)
        await self.session.send_spine(datagram)

    @staticmethod
    def _function_cache_key(address: dict[str, Any], function_name: str) -> str:
        return f"{feature_address_string(address)}::{function_name}"

    def cached_function_data(
        self, function_name: str, source: dict[str, Any]
    ) -> list[Any]:
        """Return the latest reply/notification received for a remote feature."""
        key = self._function_cache_key(source, function_name)
        if key not in self._remote_function_cache:
            return []
        return [self._remote_function_cache[key]]

    def _select_specification_version(
        self,
        header_version: Any = None,
        discovery: dict[str, Any] | None = None,
    ) -> None:
        if (
            isinstance(header_version, str)
            and header_version in SUPPORTED_SPECIFICATION_VERSIONS
        ):
            self._specification_version = header_version
        if not isinstance(discovery, dict):
            return
        version_data = discovery.get("specificationVersionList")
        if not isinstance(version_data, dict):
            return
        versions = version_data.get("specificationVersion", [])
        if isinstance(versions, str):
            versions = [versions]
        if not isinstance(versions, list):
            return
        self._peer_specification_versions = [
            version for version in versions if isinstance(version, str)
        ]
        common = [
            version
            for version in SUPPORTED_SPECIFICATION_VERSIONS
            if version in self._peer_specification_versions
        ]
        if common and self._specification_version not in common:
            self._specification_version = common[-1]

    def _local_source_for_destination(self, destination: dict[str, Any]) -> dict[str, Any]:
        source: dict[str, Any] = {"device": self.local_device_address()}
        if "entity" in destination:
            source["entity"] = destination["entity"]
        if "feature" in destination:
            source["feature"] = destination["feature"]
        return source

    def build_local_detailed_discovery(self) -> dict[str, Any]:
        """Build the local SPINE detailed discovery payload exposed to the peer."""
        return self._spine_profile.build_local_detailed_discovery()

    def build_local_destination_list(self) -> dict[str, Any]:
        return self._spine_profile.build_local_destination_list()

    def _build_hems_reference_detailed_discovery(self, local_device: str) -> dict[str, Any]:
        return self._spine_profile._build_hems_reference_detailed_discovery(local_device)

    def _build_cls_adapter_detailed_discovery(self, local_device: str) -> dict[str, Any]:
        return self._spine_profile._build_cls_adapter_detailed_discovery(local_device)

    def _remote_node_management_destination(self) -> dict[str, Any]:
        if self._uses_structured_server_profile():
            destination: dict[str, Any] = {"entity": [0], "feature": 0}
            if self._remote_device_address is not None:
                destination["device"] = self._remote_device_address
            return destination
        if self._remote_device_address is None:
            raise ValueError("remote device address is unknown; no SPINE datagram received from peer yet")
        return {"device": self._remote_device_address, "entity": [0], "feature": 0}

    def _outbound_read_ack_request(self) -> bool | None:
        # SPINE READ is answered by REPLY.  A separate RESULT acknowledgement
        # is requested for CALL/WRITE, but not for ordinary reads.
        return None

    @property
    def _profile_heartbeat_counter(self) -> int:
        return self._spine_profile._profile_heartbeat_counter

    @_profile_heartbeat_counter.setter
    def _profile_heartbeat_counter(self, value: int) -> None:
        self._spine_profile._profile_heartbeat_counter = value

    @property
    def _profile_load_control_limit_payload(self) -> dict[str, Any] | list[Any] | None:
        return self._spine_profile._profile_load_control_limit_payload

    @_profile_load_control_limit_payload.setter
    def _profile_load_control_limit_payload(self, value: dict[str, Any] | list[Any] | None) -> None:
        self._spine_profile._profile_load_control_limit_payload = value

    @property
    def _profile_device_configuration_payload(self) -> dict[str, Any] | list[Any] | None:
        return self._spine_profile._profile_device_configuration_payload

    @_profile_device_configuration_payload.setter
    def _profile_device_configuration_payload(self, value: dict[str, Any] | list[Any] | None) -> None:
        self._spine_profile._profile_device_configuration_payload = value

    @property
    def _profile_bindings(self) -> list[dict[str, Any]]:
        return self._spine_profile._profile_bindings

    @_profile_bindings.setter
    def _profile_bindings(self, value: list[dict[str, Any]]) -> None:
        self._spine_profile._profile_bindings = value

    @property
    def _profile_subscriptions(self) -> list[dict[str, Any]]:
        return self._spine_profile._profile_subscriptions

    @_profile_subscriptions.setter
    def _profile_subscriptions(self, value: list[dict[str, Any]]) -> None:
        self._spine_profile._profile_subscriptions = value

    @staticmethod
    def _profile_supported_read(function_name: str) -> dict[str, Any]:
        return ClientSpineProfile._profile_supported_read(function_name)

    @staticmethod
    def _profile_supported_read_write(function_name: str) -> dict[str, Any]:
        return ClientSpineProfile._profile_supported_read_write(function_name)

    @staticmethod
    def _profile_supported_read_partial_write(function_name: str) -> dict[str, Any]:
        return ClientSpineProfile._profile_supported_read_partial_write(function_name)

    @staticmethod
    def _profile_supported_partial_read(function_name: str) -> dict[str, Any]:
        return ClientSpineProfile._profile_supported_partial_read(function_name)

    @staticmethod
    def _profile_supported_partial_read_write(function_name: str) -> dict[str, Any]:
        return ClientSpineProfile._profile_supported_partial_read_write(function_name)

    def _profile_feature_description(self, **kwargs: Any) -> dict[str, Any]:
        return self._spine_profile._profile_feature_description(**kwargs)

    def _profile_entity_description(self, **kwargs: Any) -> dict[str, Any]:
        return self._spine_profile._profile_entity_description(**kwargs)

    @staticmethod
    def _utc_timestamp() -> str:
        return ClientSpineProfile._utc_timestamp()

    @staticmethod
    def _scaled_number(value: int, scale: int = 0) -> dict[str, int]:
        return ClientSpineProfile._scaled_number(value, scale)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        return ClientSpineProfile._format_duration(seconds)

    def _ensure_profile_runtime_defaults(self) -> None:
        self._spine_profile._ensure_profile_runtime_defaults()

    def _profile_device_classification_data(self) -> dict[str, Any]:
        return self._spine_profile._profile_device_classification_data()

    def _profile_use_case_data(self) -> dict[str, Any]:
        return self._spine_profile._profile_use_case_data()

    def _profile_load_control_limit_description_data(self) -> dict[str, Any]:
        return self._spine_profile._profile_load_control_limit_description_data()

    def _profile_device_configuration_key_value_description_data(
        self,
        address: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._spine_profile._profile_device_configuration_key_value_description_data(address)

    def _profile_device_configuration_key_value_data(
        self,
        address: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        return self._spine_profile._profile_device_configuration_key_value_data(address)

    def _profile_device_diagnosis_heartbeat_data(self) -> dict[str, Any]:
        return self._spine_profile._profile_device_diagnosis_heartbeat_data()

    def _profile_electrical_connection_characteristic_data(self) -> dict[str, Any]:
        return self._spine_profile._profile_electrical_connection_characteristic_data()

    @staticmethod
    def _profile_entity_id(address: dict[str, Any] | None) -> int | None:
        return ClientSpineProfile._profile_entity_id(address)

    def _profile_positive_energy_direction(self, address: dict[str, Any] | None) -> str:
        return self._spine_profile._profile_positive_energy_direction(address)

    def _profile_electrical_connection_description_data(
        self,
        address: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._spine_profile._profile_electrical_connection_description_data(address)

    def _profile_electrical_connection_parameter_description_data(
        self,
        address: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._spine_profile._profile_electrical_connection_parameter_description_data(address)

    def _profile_measurement_id(self, address: dict[str, Any] | None) -> int:
        return self._spine_profile._profile_measurement_id(address)

    def _profile_measurement_description_data(self, address: dict[str, Any] | None) -> dict[str, Any]:
        return self._spine_profile._profile_measurement_description_data(address)

    def _profile_measurement_constraints_data(self, address: dict[str, Any] | None) -> dict[str, Any]:
        return self._spine_profile._profile_measurement_constraints_data(address)

    def _profile_measurement_data(self, address: dict[str, Any] | None) -> dict[str, Any]:
        return self._spine_profile._profile_measurement_data(address)

    def _profile_reply_payload_for_read(
        self,
        command_name: str,
        address: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._spine_profile._profile_reply_payload_for_read(command_name, address)

    @staticmethod
    def _merge_keyed_list_payload(
        current: dict[str, Any] | list[Any] | None,
        incoming: Any,
        *,
        list_key: str,
        id_key: str,
    ) -> Any:
        return ClientSpineProfile._merge_keyed_list_payload(
            current,
            incoming,
            list_key=list_key,
            id_key=id_key,
        )

    def _record_profile_write_data(self, commands: list[dict[str, Any]]) -> None:
        self._spine_profile._record_profile_write_data(commands)

    def _normalize_feature_address(
        self,
        address: dict[str, Any],
        *,
        default_device: str | None = None,
    ) -> dict[str, Any]:
        return self._spine_profile._normalize_feature_address(address, default_device=default_device)

    def _record_profile_node_management_call(self, commands: list[dict[str, Any]], source_device: str | None) -> None:
        self._spine_profile._record_profile_node_management_call(commands, source_device)

    def _profile_notify_commands_for_feature_type(
        self,
        feature_type: str,
        server_address: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return self._spine_profile._profile_notify_commands_for_feature_type(feature_type, server_address)

    def _initial_notify_datagrams_for_subscription_calls(
        self,
        commands: list[dict[str, Any]],
        *,
        source_device: str | None,
        specification_version: str,
    ) -> list[SpineDatagram]:
        outgoing: list[SpineDatagram] = []
        for command in commands:
            request = command.get("nodeManagementSubscriptionRequestCall")
            if not isinstance(request, dict):
                continue
            subscription = request.get("subscriptionRequest")
            if not isinstance(subscription, dict):
                continue
            feature_type = subscription.get("serverFeatureType")
            if not isinstance(feature_type, str):
                continue
            client_address = self._normalize_feature_address(
                subscription.get("clientAddress", {}),
                default_device=source_device,
            )
            server_address = self._normalize_feature_address(
                subscription.get("serverAddress", {}),
                default_device=self.local_device_address(),
            )
            notify_commands = self._profile_notify_commands_for_feature_type(feature_type, server_address)
            if not notify_commands:
                continue
            outgoing.append(
                build_datagram(
                    source=server_address,
                    destination=client_address,
                    cmd_classifier="notify",
                    msg_counter=self._next_msg_counter(),
                    commands=notify_commands,
                    specification_version=specification_version,
                )
            )
        return outgoing

    def _build_subscription_request_call(
        self,
        *,
        client_address: dict[str, Any],
        server_address: dict[str, Any],
        server_feature_type: str,
    ) -> SpineDatagram:
        return build_datagram(
            source=self.local_node_management_address(),
            destination=self._remote_node_management_destination(),
            cmd_classifier="call",
            msg_counter=self._next_msg_counter(),
            ack_request=True,
            specification_version=self._specification_version,
            commands=[
                {
                    "nodeManagementSubscriptionRequestCall": {
                        "subscriptionRequest": {
                            "clientAddress": client_address,
                            "serverAddress": server_address,
                            "serverFeatureType": server_feature_type,
                        }
                    }
                }
            ],
        )

    @staticmethod
    def _feature_address_key(address: dict[str, Any]) -> str:
        return feature_address_string(address)

    def _remote_feature_addresses_from_last_discovery(
        self,
        *,
        feature_type: str,
        role: str | None = None,
        entity: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        if self._last_remote_discovery is None:
            return []
        results = self._feature_addresses(self._last_remote_discovery, feature_type=feature_type, role=role)
        if entity is None:
            return results
        return [address for address in results if address.get("entity") == entity]

    def _post_discovery_bootstrap(self) -> list[SpineDatagram]:
        if self._uses_cls_adapter_profile():
            return []
        if self._node_management_bootstrap_sent:
            return []
        self._node_management_bootstrap_sent = True
        outgoing: list[SpineDatagram] = []
        if not self._initial_node_management_subscription_sent:
            self._initial_node_management_subscription_sent = True
            outgoing.append(
                self._build_subscription_request_call(
                    client_address=self.local_node_management_address(),
                    server_address=self._remote_node_management_destination(),
                    server_feature_type="NodeManagement",
                )
            )
        outgoing.append(
            build_read_datagram(
                source=self.local_node_management_address(),
                destination=self._remote_node_management_destination(),
                msg_counter=self._next_msg_counter(),
                function_name="nodeManagementUseCaseData",
                ack_request=self._outbound_read_ack_request(),
                specification_version=self._specification_version,
            )
        )
        remote_diag_servers = self._remote_feature_addresses_from_last_discovery(
            feature_type="DeviceDiagnosis",
            role="server",
        )
        if len(remote_diag_servers) == 1:
            outgoing.extend(self._device_diagnosis_bootstrap_for_server(remote_diag_servers[0]))
        return outgoing

    def _initial_profile_read_bootstrap(self, commands: list[dict[str, Any]]) -> list[SpineDatagram]:
        if self._uses_cls_adapter_profile():
            return []
        if not self._uses_structured_server_profile():
            return []
        if self._initial_node_management_subscription_sent:
            return []
        if self._remote_device_address is None:
            return []
        if not any("nodeManagementDetailedDiscoveryData" in command for command in commands):
            return []
        self._initial_node_management_subscription_sent = True
        return [
            self._build_subscription_request_call(
                client_address=self.local_node_management_address(),
                server_address=self._remote_node_management_destination(),
                server_feature_type="NodeManagement",
            )
        ]

    def _device_diagnosis_bootstrap_for_server(self, server_address: dict[str, Any]) -> list[SpineDatagram]:
        key = self._feature_address_key(server_address)
        if key in self._remote_device_diagnosis_bootstrapped:
            return []
        self._remote_device_diagnosis_bootstrapped.add(key)
        return [
            self._build_subscription_request_call(
                client_address=self.local_device_diagnosis_client_address(),
                server_address=server_address,
                server_feature_type="DeviceDiagnosis",
            ),
            build_read_datagram(
                source=self.local_device_diagnosis_client_address(),
                destination=server_address,
                msg_counter=self._next_msg_counter(),
                function_name="deviceDiagnosisHeartbeatData",
                ack_request=self._outbound_read_ack_request(),
                specification_version=self._specification_version,
            ),
        ]

    def _binding_heartbeat_bootstrap(self) -> list[SpineDatagram]:
        if not self._profile_bindings:
            return []
        binding = self._profile_bindings[-1]
        client_address = binding.get("clientAddress")
        if not isinstance(client_address, dict):
            return []
        entity = client_address.get("entity")
        candidates = self._remote_feature_addresses_from_last_discovery(
            feature_type="DeviceDiagnosis",
            role="server",
            entity=entity if isinstance(entity, list) else None,
        )
        if len(candidates) != 1:
            all_candidates = self._remote_feature_addresses_from_last_discovery(
                feature_type="DeviceDiagnosis",
                role="server",
            )
            if len(all_candidates) != 1:
                return []
            candidates = all_candidates
        return self._device_diagnosis_bootstrap_for_server(candidates[0])

    def _should_skip_result_for_read(self, datagram: SpineDatagram, commands: list[dict[str, Any]]) -> bool:
        if not self._uses_structured_server_profile():
            return False
        header = extract_header(datagram)
        if header.get("cmdClassifier") != "read":
            return False
        return True

    @staticmethod
    def _extract_write_reply_commands(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reply_commands: list[dict[str, Any]] = []
        for command in commands:
            if "loadControlLimitListData" in command:
                reply_commands.append({"loadControlLimitListData": command["loadControlLimitListData"]})
            elif "deviceConfigurationKeyValueListData" in command:
                reply_commands.append({"deviceConfigurationKeyValueListData": command["deviceConfigurationKeyValueListData"]})
        return reply_commands

    async def _receive_and_process(self, *, timeout: float | None = None) -> SpineDatagram:
        datagram = await self.session.receive_datagram(timeout=timeout)
        self._remember_traffic("rx", datagram)
        await self.handle_incoming_datagram(datagram)
        return datagram

    async def handle_incoming_datagram(self, datagram: SpineDatagram) -> list[SpineDatagram]:
        header = extract_header(datagram)
        self._select_specification_version(header.get("specificationVersion"))
        source = header.get("addressSource", {})
        destination = header.get("addressDestination", {})
        if isinstance(source, dict) and isinstance(source.get("device"), str):
            self._remote_device_address = source["device"]

        outgoing: list[SpineDatagram] = []
        local_source = self._local_source_for_destination(destination if isinstance(destination, dict) else {})
        commands = extract_commands(datagram)
        if (
            header.get("cmdClassifier") in {"reply", "notify", "write"}
            and isinstance(source, dict)
        ):
            for command in commands:
                function_name = self._command_name(command)
                payload = command.get(function_name)
                if function_name != "unknown" and payload is not None:
                    self._remote_function_cache[
                        self._function_cache_key(source, function_name)
                    ] = payload
        needs_result = bool(header.get("ackRequest"))
        if header.get("cmdClassifier") == "call":
            needs_result = True
        if needs_result and not self._should_skip_result_for_read(datagram, commands):
            outgoing.append(
                build_result_datagram(
                    datagram,
                    source=local_source,
                    msg_counter=self._next_msg_counter(),
                )
            )

        if header.get("cmdClassifier") == "read":
            reply_commands: list[dict[str, Any]] = []
            for command in commands:
                command_name = command.get("function")
                if not isinstance(command_name, str):
                    command_name = next(
                        (key for key in command if key not in {"filter", "function"}),
                        None,
                    )
                if not isinstance(command_name, str):
                    continue
                reply_command = self._profile_reply_payload_for_read(command_name, local_source)
                if reply_command is not None:
                    reply_commands.append(reply_command)
                elif command_name == "nodeManagementDetailedDiscoveryData":
                    reply_commands.append(
                        {"nodeManagementDetailedDiscoveryData": self.build_local_detailed_discovery()}
                    )
            if reply_commands:
                outgoing.append(
                    build_reply_datagram(
                        datagram,
                        source=(
                            self.local_node_management_address()
                            if any("nodeManagement" in next(iter(cmd), "") for cmd in reply_commands)
                            else local_source
                        ),
                        msg_counter=self._next_msg_counter(),
                        commands=reply_commands,
                    )
                )
                outgoing.extend(self._initial_profile_read_bootstrap(commands))
        elif header.get("cmdClassifier") == "write":
            self._record_profile_write_data(commands)
            reply_commands = self._extract_write_reply_commands(commands)
            if reply_commands:
                outgoing.append(
                    build_reply_datagram(
                        datagram,
                        source=local_source,
                        msg_counter=self._next_msg_counter(),
                        commands=reply_commands,
                    )
                )
        elif header.get("cmdClassifier") == "call":
            self._record_profile_node_management_call(
                commands,
                source.get("device") if isinstance(source, dict) else None,
            )
            outgoing.extend(
                self._initial_notify_datagrams_for_subscription_calls(
                    commands,
                    source_device=source.get("device") if isinstance(source, dict) else None,
                    specification_version=header.get("specificationVersion", "1.3.0"),
                )
            )
            outgoing.extend(self._binding_heartbeat_bootstrap())

        discovery_payloads = extract_discovery_payloads(datagram)
        if discovery_payloads:
            for payload in discovery_payloads:
                if isinstance(payload, dict):
                    self._last_remote_discovery = payload
                    self._select_specification_version(discovery=payload)
            if header.get("cmdClassifier") in {"reply", "notify"}:
                outgoing.extend(self._post_discovery_bootstrap())

        for response in outgoing:
            await self._send_spine(response)
        return outgoing

    async def bootstrap_spine(self, *, timeout: float = 3.0) -> list[SpineDatagram]:
        """Drain and process incoming bootstrap traffic for a bounded amount of time."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        received: list[SpineDatagram] = []
        while loop.time() < deadline:
            remaining = max(0.1, deadline - loop.time())
            try:
                datagram = await self._receive_and_process(timeout=remaining)
            except asyncio.TimeoutError:
                break
            received.append(datagram)
        return received

    async def _collect_matching_payloads(
        self,
        *,
        extractor: Callable[[SpineDatagram], list[Any]],
        timeout: float,
        msg_counter_reference: int | None = None,
    ) -> list[Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        matches: list[Any] = []
        while loop.time() < deadline:
            remaining = max(0.1, deadline - loop.time())
            datagram = await self._receive_and_process(timeout=remaining)
            header = extract_header(datagram)
            if (
                msg_counter_reference is not None
                and header.get("cmdClassifier") == "result"
                and header.get("msgCounterReference") == msg_counter_reference
            ):
                for command in extract_commands(datagram):
                    result = command.get("resultData")
                    if not isinstance(result, dict):
                        continue
                    error_number = int(result.get("errorNumber", 0))
                    if error_number:
                        description = result.get("description")
                        raise SpineResultError(
                            error_number,
                            str(description) if description is not None else None,
                        )
            for payload in extractor(datagram):
                if isinstance(payload, (dict, list)):
                    matches.append(payload)
            if matches:
                return matches
        return matches

    async def request_remote_detailed_discovery(self, *, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Request and collect remote ``nodeManagementDetailedDiscoveryData`` payloads."""
        if self._remote_device_address is None and not self._uses_structured_server_profile():
            loop = asyncio.get_running_loop()
            deadline = loop.time() + min(timeout, 1.0)
            while self._remote_device_address is None and loop.time() < deadline:
                remaining = max(0.1, deadline - loop.time())
                await self._receive_and_process(timeout=remaining)

        await self._send_spine(
            build_read_datagram(
                source=self.local_node_management_address(),
                destination=self._remote_node_management_destination(),
                msg_counter=self._next_msg_counter(),
                function_name="nodeManagementDetailedDiscoveryData",
                ack_request=self._outbound_read_ack_request(),
                specification_version=self._specification_version,
            )
        )
        return await self._collect_matching_payloads(
            extractor=lambda datagram: extract_discovery_payloads(datagram),
            timeout=timeout,
        )

    def _feature_addresses(
        self,
        discovery_payload: dict[str, Any],
        *,
        feature_type: str,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return feature_addresses(
            discovery_payload,
            feature_type=feature_type,
            role=role,
            default_device=self._remote_device_address,
        )

    async def _request_function_data(
        self,
        *,
        source: dict[str, Any],
        destination: dict[str, Any],
        function_name: str,
        extractor: Callable[[SpineDatagram], list[Any]],
        timeout: float,
        partial: bool = False,
        selectors: dict[str, Any] | None = None,
    ) -> list[Any]:
        msg_counter = self._next_msg_counter()
        await self._send_spine(
            build_read_datagram(
                source=source,
                destination=destination,
                msg_counter=msg_counter,
                function_name=function_name,
                partial=partial,
                selectors=selectors,
                ack_request=self._outbound_read_ack_request(),
                specification_version=self._specification_version,
            )
        )
        try:
            payloads = await self._collect_matching_payloads(
                extractor=extractor,
                timeout=timeout,
                msg_counter_reference=msg_counter,
            )
        except asyncio.TimeoutError:
            cached = self.cached_function_data(function_name, destination)
            if cached:
                return cached
            raise
        return payloads or self.cached_function_data(function_name, destination)

    async def discover_nodes(self, *, timeout: float = 5.0) -> list[dict]:
        """Fetch the peer's detailed discovery payloads."""
        return await self.request_remote_detailed_discovery(timeout=timeout)

    async def read_remote_measurements(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """Discover remote measurement features and read descriptions plus current values."""
        discovery_payloads = await self.request_remote_detailed_discovery(timeout=max(1.0, timeout / 3))
        discovery = discovery_payloads[-1] if discovery_payloads else {}
        measurement_features = self._feature_addresses(discovery, feature_type="Measurement", role="server")

        descriptions: list[dict[str, Any]] = []
        values: list[dict[str, Any]] = []
        for address in measurement_features:
            if not descriptions:
                descriptions = await self._request_function_data(
                    source=self.local_measurement_client_address(),
                    destination=address,
                    function_name="measurementDescriptionListData",
                    extractor=lambda datagram: extract_measurement_descriptions(datagram),
                    timeout=max(1.0, timeout / 3),
                )
            if not values:
                values = await self._request_function_data(
                    source=self.local_measurement_client_address(),
                    destination=address,
                    function_name="measurementListData",
                    extractor=lambda datagram: extract_measurement_payloads(datagram),
                    timeout=max(1.0, timeout / 3),
                )
            if values:
                break

        return {
            "remote_device_address": self._remote_device_address,
            "discovery": discovery,
            "measurement_features": measurement_features,
            "measurement_descriptions": descriptions,
            "measurement_payloads": values,
        }

    async def subscribe_updates(self) -> AsyncIterator[SpineDatagram]:
        """Yield incoming SPINE datagrams from the active SHIP session."""
        async for event in self.session.events():
            if event.kind == "datagram":
                yield event.payload

    async def send_datagram(self, payload: SpineDatagram | dict) -> None:
        """Send one SPINE datagram to the connected peer."""
        await self._send_spine(payload)

    async def session_events(self) -> AsyncIterator[ShipEvent]:
        """Expose the raw SHIP event stream for advanced integrations."""
        async for event in self.session.events():
            yield event
