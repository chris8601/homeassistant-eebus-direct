"""Direct SHIP/SPINE runtime for one EEBUS wallbox."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ._vendor.eebus_sdk import (
    EebusError,
    HemsClient,
    ShipService,
    TrustStore,
    discover_ship_services,
)
from ._vendor.eebus_sdk.spine import (
    build_datagram,
    extract_commands,
    extract_header,
)
from .const import (
    CONF_IDENTITY_PATH,
    CONF_INTERFACE_IP,
    CONF_PEER_SKI,
    CONF_SERVICE,
)
from .identity import load_identity
from .model import (
    decode_configuration,
    decode_diagnosis,
    decode_identification,
    decode_limits,
    decode_manufacturer,
    decode_measurements,
    decode_use_cases,
    entity_types,
    find_features,
    preferred_ev_entity,
)

_LOGGER = logging.getLogger(__name__)


class EebusRuntimeError(Exception):
    """A direct EEBUS operation failed."""


def service_from_dict(data: dict[str, Any]) -> ShipService:
    """Restore a serialised mDNS SHIP service."""
    allowed = {
        "service_name",
        "target",
        "port",
        "path",
        "ship_id",
        "ski",
        "brand",
        "model",
        "device_type",
        "register",
        "addresses",
        "txt",
        "tls_probe",
    }
    return ShipService(**{key: value for key, value in data.items() if key in allowed})


def _same_address(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("device") == right.get("device")
        and left.get("entity") == right.get("entity")
        and left.get("feature") == right.get("feature")
    )


def _source_address(client: HemsClient, feature_type: str) -> dict[str, Any]:
    mapping = {
        "DeviceClassification": client.local_device_classification_client_address,
        "DeviceConfiguration": client.local_device_configuration_client_address,
        "DeviceDiagnosis": client.local_device_diagnosis_client_address,
        "ElectricalConnection": client.local_electrical_connection_client_address,
        "Identification": client.local_identification_client_address,
        "LoadControl": client.local_load_control_client_address,
        "Measurement": client.local_measurement_client_address,
    }
    factory = mapping.get(feature_type)
    return factory() if factory else client.local_node_management_address()


class EebusRuntime:
    """Own the direct TLS socket, SPINE model, heartbeat, reads, and writes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.interface_ip = str(entry.data[CONF_INTERFACE_IP])
        self.peer_ski = str(entry.data[CONF_PEER_SKI]).lower()
        self.identity_path = str(entry.data[CONF_IDENTITY_PATH])
        self.service = service_from_dict(dict(entry.data[CONF_SERVICE]))
        self.client: HemsClient | None = None
        self._lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._discovery: dict[str, Any] = {}
        self._static_signature: tuple[Any, ...] | None = None
        self._static: dict[str, Any] = {}
        self._target_current_a: float | None = None
        self._target_phase_a: dict[str, float] = {}
        self._solar_current_a: float | None = None
        self._last_state: dict[str, Any] = {}

    async def _async_rediscover(self) -> ShipService:
        try:
            services = await self.hass.async_add_executor_job(
                partial(discover_ship_services, self.interface_ip, timeout=3.0)
            )
        except (EebusError, OSError) as exc:
            _LOGGER.debug("EEBUS rediscovery failed; using stored address: %s", exc)
            return self.service
        for service in services:
            if (service.ski or "").lower() == self.peer_ski:
                self.service = service
                return service
        return self.service

    async def _async_connect(self) -> None:
        if self.client is not None:
            return
        identity = await self.hass.async_add_executor_job(
            load_identity, self.identity_path
        )
        service = await self._async_rediscover()
        trust = TrustStore.from_server_ski(self.peer_ski, verify_tls=False)
        try:
            client = await HemsClient.connect(
                service,
                identity,
                trust,
                interface_ip=self.interface_ip,
                pairing_wait_seconds=15,
                timeout=12.0,
                profile="default",
            )
            await client.bootstrap_spine(timeout=2.0)
        except Exception as exc:
            raise EebusRuntimeError(
                f"SHIP-Verbindung zur Wallbox fehlgeschlagen: {exc}"
            ) from exc
        self.client = client
        self._heartbeat_task = self.entry.async_create_background_task(
            self.hass,
            self._heartbeat_loop(),
            name=f"eebus_heartbeat_{self.peer_ski[:8]}",
        )

    async def _async_disconnect(self) -> None:
        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        client = self.client
        self.client = None
        if client is not None:
            with suppress(Exception):
                await client.close()

    async def async_close(self) -> None:
        """Close the active SHIP connection."""
        async with self._lock:
            await self._async_disconnect()

    async def async_reconnect(self) -> None:
        """Force rediscovery and a fresh SHIP connection."""
        async with self._lock:
            await self._async_disconnect()
            self._static_signature = None
            await self._async_connect()

    async def _heartbeat_loop(self) -> None:
        """Serve the four-second OPEV/OSCEV heartbeat contract."""
        while True:
            await asyncio.sleep(2.0)
            client = self.client
            if client is None:
                return
            local_server = client.local_device_diagnosis_server_address()
            subscriptions = list(client._profile_subscriptions)
            destinations: list[dict[str, Any]] = []
            for subscription in subscriptions:
                server = subscription.get("serverAddress")
                destination = subscription.get("clientAddress")
                if (
                    isinstance(server, dict)
                    and isinstance(destination, dict)
                    and _same_address(server, local_server)
                ):
                    destinations.append(destination)
            for destination in destinations:
                try:
                    await client.send_datagram(
                        build_datagram(
                            source=local_server,
                            destination=destination,
                            cmd_classifier="notify",
                            msg_counter=client._next_msg_counter(),
                            commands=[
                                {
                                    "deviceDiagnosisHeartbeatData": client._profile_device_diagnosis_heartbeat_data()
                                },
                                {
                                    "deviceDiagnosisStateData": {
                                        "operatingState": "normalOperation"
                                    }
                                },
                            ],
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - heartbeat task must survive peer errors
                    _LOGGER.debug("Could not send EEBUS heartbeat: %s", exc)

    async def _read_function(
        self,
        feature_type: str,
        function_name: str,
        *,
        entity: list[int] | None = None,
        timeout: float = 2.5,
    ) -> dict[str, Any] | None:
        client = self.client
        if client is None:
            raise EebusRuntimeError("EEBUS client is not connected")
        candidates = find_features(
            self._discovery,
            feature_type,
            entity=entity,
            function_name=function_name,
            default_device=client._remote_device_address,
        )
        if not candidates and entity is not None:
            candidates = find_features(
                self._discovery,
                feature_type,
                function_name=function_name,
                default_device=client._remote_device_address,
            )
        if not candidates:
            return None
        # Prefer the deepest EV child over the EVSE/root feature.
        destination = max(
            (feature["address"] for feature in candidates),
            key=lambda address: len(address.get("entity") or []),
        )

        def extractor(datagram: Any) -> list[Any]:
            return [
                command[function_name]
                for command in extract_commands(datagram)
                if function_name in command
            ]

        try:
            payloads = await client._request_function_data(
                source=_source_address(client, feature_type),
                destination=destination,
                function_name=function_name,
                extractor=extractor,
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None
        return payloads[-1] if payloads else None

    async def _read_discovery(self) -> None:
        assert self.client is not None
        payloads = await self.client.request_remote_detailed_discovery(timeout=4.0)
        discovery = next(
            (
                payload
                for payload in reversed(payloads)
                if "featureInformation" in payload
            ),
            self.client._last_remote_discovery,
        )
        if not isinstance(discovery, dict) or "featureInformation" not in discovery:
            raise EebusRuntimeError("Wallbox liefert keine SPINE-Gerätebeschreibung")
        self._discovery = discovery

    async def _read_use_cases(self) -> dict[str, Any] | None:
        client = self.client
        assert client is not None
        try:
            payloads = await client._request_function_data(
                source=client.local_node_management_address(),
                destination=client._remote_node_management_destination(),
                function_name="nodeManagementUseCaseData",
                extractor=lambda datagram: [
                    command["nodeManagementUseCaseData"]
                    for command in extract_commands(datagram)
                    if "nodeManagementUseCaseData" in command
                ],
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            return None
        return payloads[-1] if payloads else None

    def _signature(self) -> tuple[Any, ...]:
        entities = tuple(sorted(entity_types(self._discovery).items()))
        features = tuple(
            sorted(
                (
                    feature.get("description", {}).get("featureType"),
                    tuple(
                        feature.get("description", {})
                        .get("featureAddress", {})
                        .get("entity", [])
                    ),
                    feature.get("description", {})
                    .get("featureAddress", {})
                    .get("feature"),
                )
                for feature in self._discovery.get("featureInformation", [])
                if isinstance(feature, dict)
            )
        )
        return entities, features

    async def _bind_and_subscribe(self) -> None:
        """Commission readable/writable remote features at NodeManagement."""
        client = self.client
        assert client is not None
        remote_nm = client._remote_node_management_destination()
        bindings: list[tuple[str, dict[str, Any]]] = []
        subscriptions: list[tuple[str, dict[str, Any]]] = []
        for feature_type in ("LoadControl",):
            for feature in find_features(
                self._discovery,
                feature_type,
                default_device=client._remote_device_address,
            ):
                bindings.append((feature_type, feature["address"]))
        for feature_type in ("DeviceDiagnosis", "LoadControl", "Measurement"):
            for feature in find_features(
                self._discovery,
                feature_type,
                default_device=client._remote_device_address,
            ):
                subscriptions.append((feature_type, feature["address"]))

        for feature_type, server in bindings:
            await client.send_datagram(
                build_datagram(
                    source=client.local_node_management_address(),
                    destination=remote_nm,
                    cmd_classifier="call",
                    msg_counter=client._next_msg_counter(),
                    ack_request=True,
                    commands=[
                        {
                            "nodeManagementBindingRequestCall": {
                                "bindingRequest": {
                                    "clientAddress": _source_address(
                                        client, feature_type
                                    ),
                                    "serverAddress": server,
                                    "serverFeatureType": feature_type,
                                }
                            }
                        }
                    ],
                )
            )
        for feature_type, server in subscriptions:
            await client.send_datagram(
                client._build_subscription_request_call(
                    client_address=_source_address(client, feature_type),
                    server_address=server,
                    server_feature_type=feature_type,
                )
            )
        await client.bootstrap_spine(timeout=1.0)

    async def _read_static(self, ev_entity: list[int] | None) -> None:
        evse_entities = [
            list(address)
            for address, kind in entity_types(self._discovery).items()
            if kind == "EVSE"
        ]
        manufacturer_entity = evse_entities[0] if evse_entities else ev_entity
        self._static = {
            "use_cases": await self._read_use_cases(),
            "manufacturer": await self._read_function(
                "DeviceClassification",
                "deviceClassificationManufacturerData",
                entity=manufacturer_entity,
            ),
            "measurement_descriptions": await self._read_function(
                "Measurement", "measurementDescriptionListData", entity=ev_entity
            ),
            "electrical_parameters": await self._read_function(
                "ElectricalConnection",
                "electricalConnectionParameterDescriptionListData",
                entity=ev_entity,
            ),
            "permitted_values": await self._read_function(
                "ElectricalConnection",
                "electricalConnectionPermittedValueSetListData",
                entity=ev_entity,
            ),
            "characteristics": await self._read_function(
                "ElectricalConnection",
                "electricalConnectionCharacteristicListData",
                entity=ev_entity,
            ),
            "limit_descriptions": await self._read_function(
                "LoadControl", "loadControlLimitDescriptionListData", entity=ev_entity
            ),
            "configuration_descriptions": await self._read_function(
                "DeviceConfiguration",
                "deviceConfigurationKeyValueDescriptionListData",
                entity=ev_entity,
            ),
        }
        self._static["identification"] = await self._read_function(
            "Identification", "identificationListData", entity=ev_entity
        )
        await self._bind_and_subscribe()

    async def _collect_state(self) -> dict[str, Any]:
        await self._read_discovery()
        signature = self._signature()
        ev_entity = preferred_ev_entity(self._discovery)
        if signature != self._static_signature:
            self._static_signature = signature
            await self._read_static(ev_entity)

        dynamic = {
            "measurements": await self._read_function(
                "Measurement", "measurementListData", entity=ev_entity
            ),
            "limits": await self._read_function(
                "LoadControl", "loadControlLimitListData", entity=ev_entity
            ),
            "diagnosis": await self._read_function(
                "DeviceDiagnosis", "deviceDiagnosisStateData", entity=ev_entity
            ),
            "heartbeat": await self._read_function(
                "DeviceDiagnosis", "deviceDiagnosisHeartbeatData", entity=ev_entity
            ),
            "configuration": await self._read_function(
                "DeviceConfiguration",
                "deviceConfigurationKeyValueListData",
                entity=ev_entity,
            ),
        }
        measurement = decode_measurements(
            self._static.get("measurement_descriptions"),
            self._static.get("electrical_parameters"),
            dynamic["measurements"],
        )
        limits = decode_limits(
            self._static.get("limit_descriptions"),
            dynamic["limits"],
            self._static.get("electrical_parameters"),
            self._static.get("permitted_values"),
        )
        diagnosis = decode_diagnosis([dynamic["diagnosis"], dynamic["heartbeat"]])
        use_cases = decode_use_cases(self._static.get("use_cases"))
        manufacturer = decode_manufacturer(self._static.get("manufacturer"))
        configuration = decode_configuration(
            self._static.get("configuration_descriptions"), dynamic["configuration"]
        )
        identifications = decode_identification(self._static.get("identification"))
        entities = entity_types(self._discovery)
        obligation = limits["obligation"]
        recommendation = limits["recommendation"]
        applied_current = obligation.get("value_a")
        if self._target_current_a is None and isinstance(applied_current, (int, float)):
            self._target_current_a = float(applied_current)
        power = float(measurement.get("power_w") or 0)
        current = float(measurement.get("current_a") or 0)
        operating_state = str(diagnosis.get("operatingState") or "unknown")
        fault = operating_state.lower() in {"failure", "error"}
        vehicle_connected = any(kind == "EV" for kind in entities.values())
        charging = power > 50 or current > 0.5
        enabled = not obligation.get("active") or float(applied_current or 0) >= 1
        status = (
            "fault"
            if fault
            else "charging"
            if charging
            else "ready"
            if vehicle_connected
            else "idle"
        )

        state: dict[str, Any] = {
            "connected": True,
            "status": status,
            "peer": {
                "ski": self.peer_ski,
                "ship_id": self.service.ship_id,
                "host": self.service.preferred_host(),
                "port": self.service.port,
                "brand": manufacturer.get("brand") or self.service.brand or "Hager",
                "manufacturer": manufacturer.get("manufacturer")
                or self.service.brand
                or "Hager",
                "model": manufacturer.get("model")
                or self.service.model
                or "Witty Flow",
                "name": manufacturer.get("name")
                or self.service.model
                or "EEBUS Wallbox",
                "serial": manufacturer.get("serial")
                or self.service.ship_id
                or self.peer_ski[:12],
            },
            "capabilities": {
                "current_limit": bool(obligation.get("supported")),
                "per_phase_current": len(obligation.get("limits", {})) > 1,
                "pause": bool(obligation.get("supported")),
                "solar_recommendation": bool(recommendation.get("supported")),
                "state_of_charge": "state_of_charge_pct" in measurement,
                "vehicle_identification": bool(identifications),
                "measurements": bool(measurement.get("raw")),
            },
            "charging": {
                "enabled": enabled,
                "active": charging,
                "vehicle_connected": vehicle_connected,
                "target_current_a": self._target_current_a,
                "target_phase_a": dict(self._target_phase_a),
                "applied_current_a": applied_current,
                "min_current_a": obligation.get("min_a", 6.0),
                "max_current_a": obligation.get("max_a", 32.0),
                "solar_current_a": self._solar_current_a
                or recommendation.get("value_a"),
                "solar_active": bool(recommendation.get("active")),
            },
            "measurements": measurement,
            "vehicle": {
                "identifications": identifications,
                "configuration": configuration,
                "state_of_charge_pct": measurement.get("state_of_charge_pct"),
                "state_of_health_pct": measurement.get("state_of_health_pct"),
                "range_km": measurement.get("range_km"),
            },
            "diagnostics": {
                "operating_state": operating_state,
                "fault": fault,
                "heartbeat_counter": diagnosis.get("heartbeatCounter"),
                "heartbeat_timeout": diagnosis.get("heartbeatTimeout"),
                "use_cases": use_cases,
                "entities": {
                    ".".join(map(str, key)): value for key, value in entities.items()
                },
                "limits": limits,
            },
        }
        self._last_state = state
        return state

    async def async_update(self) -> dict[str, Any]:
        """Connect/reconnect and return a complete state snapshot."""
        async with self._lock:
            for attempt in range(2):
                try:
                    await self._async_connect()
                    return await self._collect_state()
                except (EebusError, OSError, asyncio.TimeoutError, EOFError) as exc:
                    await self._async_disconnect()
                    if attempt:
                        raise EebusRuntimeError(
                            f"EEBUS-Datenabruf fehlgeschlagen: {exc}"
                        ) from exc
                except EebusRuntimeError:
                    await self._async_disconnect()
                    if attempt:
                        raise
            raise EebusRuntimeError("EEBUS-Datenabruf fehlgeschlagen")

    async def _write_limit(
        self,
        category: str,
        amperes: float,
        *,
        phase: str | None = None,
        active: bool = True,
    ) -> None:
        client = self.client
        if client is None:
            raise EebusRuntimeError("Wallbox ist nicht verbunden")
        decoded = (
            self._last_state.get("diagnostics", {}).get("limits", {}).get(category, {})
        )
        limits = decoded.get("limits", {})
        if not decoded.get("supported") or not limits:
            raise EebusRuntimeError(
                "Die Wallbox bietet diesen EEBUS-Grenzwert nicht an"
            )
        chosen = limits
        if phase is not None:
            if phase not in limits:
                raise EebusRuntimeError(
                    f"Die Wallbox bietet für Phase {phase.upper()} keinen Grenzwert an"
                )
            chosen = {phase: limits[phase]}
        minimum = float(decoded.get("min_a") or 6)
        maximum = float(decoded.get("max_a") or 32)
        if active and amperes > 0 and not minimum <= amperes <= maximum:
            raise EebusRuntimeError(
                f"{amperes:g} A liegt außerhalb des angekündigten Bereichs {minimum:g}…{maximum:g} A"
            )
        number = round(amperes * 10)
        value = {"number": number, "scale": -1}
        data = [
            {
                "limitId": item["id"],
                "isLimitActive": active,
                **({"value": value} if active else {}),
            }
            for item in chosen.values()
            if item.get("changeable", True)
        ]
        if not data:
            raise EebusRuntimeError(
                "Die Wallbox kennzeichnet die Grenzwerte als nicht änderbar"
            )
        ev_entity = preferred_ev_entity(self._discovery)
        candidates = find_features(
            self._discovery,
            "LoadControl",
            entity=ev_entity,
            function_name="loadControlLimitListData",
            default_device=client._remote_device_address,
        )
        if not candidates:
            raise EebusRuntimeError("Kein beschreibbares LoadControl-Feature gefunden")
        destination = candidates[0]["address"]
        message_counter = client._next_msg_counter()
        datagram = build_datagram(
            source=client.local_load_control_client_address(),
            destination=destination,
            cmd_classifier="write",
            msg_counter=message_counter,
            ack_request=True,
            commands=[
                {
                    "function": "loadControlLimitListData",
                    "filter": {"cmdControl": {"partial": {}}},
                    "loadControlLimitListData": {"loadControlLimitData": data},
                }
            ],
        )
        await client.send_datagram(datagram)
        deadline = asyncio.get_running_loop().time() + 4.0
        accepted = False
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                incoming = await client._receive_and_process(timeout=remaining)
            except asyncio.TimeoutError:
                break
            header = extract_header(incoming)
            commands = extract_commands(incoming)
            for command in commands:
                result = command.get("resultData")
                if header.get("msgCounterReference") == message_counter and isinstance(
                    result, dict
                ):
                    error = int(result.get("errorNumber", 0))
                    if error:
                        raise EebusRuntimeError(
                            f"Wallbox hat den EEBUS-Schreibbefehl abgelehnt (Fehler {error})"
                        )
                    accepted = True
                if "loadControlLimitListData" in command:
                    accepted = True
            if accepted:
                break
        if not accepted:
            raise EebusRuntimeError(
                "Keine Bestätigung des EEBUS-Schreibbefehls empfangen"
            )

    async def async_set_current(self, amperes: float, phase: str | None = None) -> None:
        """Apply an OPEV obligation current ceiling, optionally to one phase."""
        async with self._lock:
            await self._async_connect()
            await self._write_limit("obligation", amperes, phase=phase, active=True)
            if phase is None:
                self._target_current_a = amperes
            else:
                self._target_phase_a[phase] = amperes

    async def async_set_charging(self, enabled: bool) -> None:
        """Pause with a 0 A obligation or resume at the selected target."""
        async with self._lock:
            await self._async_connect()
            amperes = self._target_current_a or float(
                self._last_state.get("charging", {}).get("min_current_a") or 6
            )
            await self._write_limit(
                "obligation", amperes if enabled else 0.0, active=True
            )

    async def async_set_solar_current(
        self, amperes: float, active: bool = True
    ) -> None:
        """Apply an OSCEV self-consumption recommendation."""
        async with self._lock:
            await self._async_connect()
            await self._write_limit("recommendation", amperes, active=active)
            self._solar_current_a = amperes

    async def async_release_limits(self) -> None:
        """Deactivate all OPEV and OSCEV limits managed by Home Assistant."""
        async with self._lock:
            await self._async_connect()
            for category in ("obligation", "recommendation"):
                decoded = (
                    self._last_state.get("diagnostics", {})
                    .get("limits", {})
                    .get(category, {})
                )
                if decoded.get("supported"):
                    await self._write_limit(category, 0.0, active=False)
