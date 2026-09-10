"""Pure SPINE payload decoding for EVSE and EV entities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def scaled_number(value: Any) -> float | None:
    """Decode a SPINE scaled number, accepting the common wrapper variants."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, dict):
        return None
    if "scaledNumber" in value:
        return scaled_number(value["scaledNumber"])
    number = value.get("number")
    scale = value.get("scale", 0)
    if not isinstance(number, (int, float)) or isinstance(number, bool):
        return None
    if not isinstance(scale, int):
        scale = 0
    return float(number) * (10.0**scale)


def _values_for_key(value: Any, key: str) -> Iterable[Any]:
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key == key:
                yield child
            yield from _values_for_key(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _values_for_key(child, key)


def records(payloads: Any, list_key: str) -> list[dict[str, Any]]:
    """Return every record in a SPINE ``*ListData`` payload."""
    found: list[dict[str, Any]] = []
    for value in _values_for_key(payloads, list_key):
        if isinstance(value, dict):
            found.append(value)
        elif isinstance(value, list):
            found.extend(item for item in value if isinstance(item, dict))
    return found


def entity_types(discovery: dict[str, Any]) -> dict[tuple[int, ...], str]:
    result: dict[tuple[int, ...], str] = {}
    for item in discovery.get("entityInformation", []):
        if not isinstance(item, dict):
            continue
        description = item.get("description", {})
        address = (
            description.get("entityAddress", {})
            if isinstance(description, dict)
            else {}
        )
        entity = address.get("entity") if isinstance(address, dict) else None
        entity_type = (
            description.get("entityType") if isinstance(description, dict) else None
        )
        if isinstance(entity, list) and isinstance(entity_type, str):
            result[tuple(value for value in entity if isinstance(value, int))] = (
                entity_type
            )
    return result


def preferred_ev_entity(discovery: dict[str, Any]) -> list[int] | None:
    entities = entity_types(discovery)
    ev = [address for address, kind in entities.items() if kind == "EV"]
    if ev:
        return list(max(ev, key=len))
    evse = [address for address, kind in entities.items() if kind == "EVSE"]
    return list(max(evse, key=len)) if evse else None


def feature_catalog(
    discovery: dict[str, Any], default_device: str | None = None
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for item in discovery.get("featureInformation", []):
        if not isinstance(item, dict):
            continue
        description = item.get("description")
        if not isinstance(description, dict):
            continue
        address = dict(description.get("featureAddress") or {})
        if default_device and not address.get("device"):
            address["device"] = default_device
        features.append(
            {
                "address": address,
                "type": description.get("featureType"),
                "role": description.get("role"),
                "functions": description.get("supportedFunction") or [],
                "description": description.get("description"),
            }
        )
    return features


def feature_supports(feature: dict[str, Any], function_name: str) -> bool:
    functions = feature.get("functions")
    if not functions:
        return True
    if isinstance(functions, dict):
        functions = [functions]
    for item in functions:
        if isinstance(item, dict) and item.get("function") == function_name:
            return True
    return False


def feature_supports_partial_read(
    feature: dict[str, Any], function_name: str
) -> bool:
    """Return whether discovery advertises optional partial-read support."""
    functions = feature.get("functions")
    if isinstance(functions, dict):
        functions = [functions]
    if not isinstance(functions, list):
        return False
    for item in functions:
        if not isinstance(item, dict) or item.get("function") != function_name:
            continue
        operations = item.get("possibleOperations")
        if not isinstance(operations, dict):
            return False
        read = operations.get("read")
        return isinstance(read, dict) and "partial" in read
    return False


def find_features(
    discovery: dict[str, Any],
    feature_type: str,
    *,
    role: str = "server",
    entity: list[int] | None = None,
    function_name: str | None = None,
    default_device: str | None = None,
) -> list[dict[str, Any]]:
    candidates = []
    for feature in feature_catalog(discovery, default_device):
        address = feature["address"]
        if feature.get("type") != feature_type or feature.get("role") != role:
            continue
        if entity is not None and address.get("entity") != entity:
            continue
        if function_name and not feature_supports(feature, function_name):
            continue
        candidates.append(feature)
    return candidates


def decode_use_cases(payloads: Any) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for info in records(payloads, "useCaseInformation"):
        support = info.get("useCaseSupport", [])
        if isinstance(support, dict):
            support = [support]
        for use_case in support:
            if not isinstance(use_case, dict):
                continue
            name = use_case.get("useCaseName")
            scenarios = use_case.get("scenarioSupport", [])
            if isinstance(name, str):
                result[name] = [value for value in scenarios if isinstance(value, int)]
    return result


def decode_manufacturer(payloads: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    keys = {
        "brandName": "brand",
        "vendorName": "manufacturer",
        "deviceName": "name",
        "deviceCode": "model",
        "serialNumber": "serial",
        "manufacturerNodeIdentification": "manufacturer_node_id",
        "powerSource": "power_source",
    }
    for payload in _values_for_key(payloads, "deviceClassificationManufacturerData"):
        if not isinstance(payload, dict):
            continue
        for source, target in keys.items():
            value = payload.get(source)
            if value not in (None, ""):
                result[target] = value
    return result


def decode_diagnosis(payloads: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for payload in _values_for_key(payloads, "deviceDiagnosisStateData"):
        if not isinstance(payload, dict):
            continue
        for key in ("operatingState", "lastErrorCode", "lastErrorTimestamp"):
            if key in payload:
                result[key] = payload[key]
    for payload in _values_for_key(payloads, "deviceDiagnosisHeartbeatData"):
        if not isinstance(payload, dict):
            continue
        if "heartbeatCounter" in payload:
            result["heartbeatCounter"] = payload["heartbeatCounter"]
        if "heartbeatTimeout" in payload:
            result["heartbeatTimeout"] = payload["heartbeatTimeout"]
    return result


def decode_identification(payloads: Any) -> list[dict[str, Any]]:
    return records(payloads, "identificationData")


def decode_configuration(descriptions: Any, values: Any) -> dict[str, Any]:
    description_by_id = {
        item.get("keyId"): item
        for item in records(descriptions, "deviceConfigurationKeyValueDescriptionData")
        if isinstance(item.get("keyId"), int)
    }
    result: dict[str, Any] = {}
    for item in records(values, "deviceConfigurationKeyValueData"):
        key_id = item.get("keyId")
        description = description_by_id.get(key_id, {})
        name = description.get("keyName") or f"key_{key_id}"
        raw = item.get("value")
        value: Any = scaled_number(raw)
        if value is None and isinstance(raw, dict):
            value = next(iter(raw.values()), None)
        result[str(name)] = value
    return result


def _phase_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.lower().replace("phase", "").replace("_", "")
    aliases = {"l1": "a", "l2": "b", "l3": "c", "an": "a", "bn": "b", "cn": "c"}
    return aliases.get(normalized, normalized)


def decode_measurements(
    descriptions: Any,
    parameters: Any,
    values: Any,
) -> dict[str, Any]:
    """Map arbitrary measurement IDs to stable EV charging quantities."""
    desc_by_id = {
        item.get("measurementId"): item
        for item in records(descriptions, "measurementDescriptionData")
        if isinstance(item.get("measurementId"), int)
    }
    parameter_by_measurement = {
        item.get("measurementId"): item
        for item in records(parameters, "electricalConnectionParameterDescriptionData")
        if isinstance(item.get("measurementId"), int)
    }
    result: dict[str, Any] = {"raw": []}
    phase_power: dict[str, float] = {}
    phase_current: dict[str, float] = {}
    for item in records(values, "measurementData"):
        measurement_id = item.get("measurementId")
        value = scaled_number(item.get("value"))
        if value is None:
            continue
        desc = desc_by_id.get(measurement_id, {})
        parameter = parameter_by_measurement.get(measurement_id, {})
        scope = str(desc.get("scopeType") or parameter.get("scopeType") or "")
        measurement_type = str(desc.get("measurementType") or "")
        unit = str(desc.get("unit") or "")
        phase = _phase_name(parameter.get("acMeasuredPhases"))
        normalized = value
        if unit == "kW" or unit == "kWh":
            normalized *= 1000
        result["raw"].append(
            {
                "id": measurement_id,
                "value": value,
                "unit": unit,
                "scope": scope,
                "phase": phase,
                "type": measurement_type,
            }
        )

        if scope == "acPowerTotal" or (measurement_type == "power" and phase == "abc"):
            result["power_w"] = normalized
        elif scope == "acPower" or measurement_type == "power":
            if phase in {"a", "b", "c"}:
                phase_power[phase] = normalized
        elif scope == "acCurrent" or measurement_type == "current":
            if phase in {"a", "b", "c"}:
                phase_current[phase] = normalized
                result[f"current_{phase}_a"] = normalized
            elif phase == "abc":
                result["current_a"] = normalized
        elif scope == "acVoltage" or measurement_type == "voltage":
            if phase in {"a", "b", "c"}:
                result[f"voltage_{phase}_v"] = normalized
            elif "voltage_v" not in result:
                result["voltage_v"] = normalized
        elif scope == "acFrequency" or measurement_type == "frequency":
            result["frequency_hz"] = normalized
        elif scope == "charge":
            result["session_energy_kwh"] = normalized / 1000
        elif scope in {"acEnergyConsumed", "gridConsumption"}:
            result["total_energy_kwh"] = normalized / 1000
        elif scope == "stateOfCharge":
            result["state_of_charge_pct"] = value
        elif scope == "stateOfHealth":
            result["state_of_health_pct"] = value
        elif scope == "travelRange":
            result["range_km"] = (
                value / 1000 if unit in {"m", "metre", "meter"} else value
            )

    if "power_w" not in result and phase_power:
        result["power_w"] = sum(phase_power.values())
    for phase, value in phase_power.items():
        result[f"power_{phase}_w"] = value
    if "current_a" not in result and phase_current:
        result["current_a"] = max(phase_current.values())
    return result


def charging_activity(measurements: dict[str, Any]) -> bool | None:
    """Derive charging activity only when the EVSE supplied usable samples."""
    power = measurements.get("power_w")
    current = measurements.get("current_a")
    if not isinstance(power, (int, float)) and not isinstance(current, (int, float)):
        return None
    return bool(
        (isinstance(power, (int, float)) and power > 50)
        or (isinstance(current, (int, float)) and current > 0.5)
    )


def charging_status(
    *, fault: bool, vehicle_connected: bool, charging: bool | None
) -> str | None:
    """Return a charging status without inventing a state from missing data."""
    if fault:
        return "fault"
    if charging is True:
        return "charging"
    if charging is False:
        return "ready" if vehicle_connected else "idle"
    if not vehicle_connected:
        return "idle"
    return None


def _range_values(value: Any) -> tuple[float | None, float | None, float | None]:
    minima: list[float] = []
    maxima: list[float] = []
    defaults: list[float] = []
    for range_item in records(value, "range"):
        minimum = scaled_number(range_item.get("min"))
        maximum = scaled_number(range_item.get("max"))
        if minimum is not None:
            minima.append(minimum)
        if maximum is not None:
            maxima.append(maximum)
    for item in _values_for_key(value, "value"):
        decoded = scaled_number(item)
        if decoded is not None:
            defaults.append(decoded)
    return (
        min(minima) if minima else None,
        max(maxima) if maxima else None,
        defaults[0] if defaults else None,
    )


def decode_limits(
    descriptions: Any,
    limit_values: Any,
    parameters: Any,
    permitted_values: Any,
) -> dict[str, Any]:
    """Decode OPEV obligation and OSCEV recommendation current limits."""
    parameters_by_measurement = {
        item.get("measurementId"): item
        for item in records(parameters, "electricalConnectionParameterDescriptionData")
        if isinstance(item.get("measurementId"), int)
    }
    permitted_by_parameter = {
        item.get("parameterId"): item
        for item in records(
            permitted_values, "electricalConnectionPermittedValueSetData"
        )
        if isinstance(item.get("parameterId"), int)
    }
    values_by_id = {
        item.get("limitId"): item
        for item in records(limit_values, "loadControlLimitData")
        if isinstance(item.get("limitId"), int)
    }
    result: dict[str, Any] = {
        "obligation": {"supported": False, "limits": {}},
        "recommendation": {"supported": False, "limits": {}},
    }
    for description in records(descriptions, "loadControlLimitDescriptionData"):
        limit_id = description.get("limitId")
        if not isinstance(limit_id, int):
            continue
        category = str(description.get("limitCategory") or "")
        scope = str(description.get("scopeType") or "")
        unit = str(description.get("unit") or "")
        if category == "obligation" or scope == "overloadProtection":
            bucket_name = "obligation"
        elif category == "recommendation" or scope == "selfConsumption":
            bucket_name = "recommendation"
        else:
            continue
        if unit and unit != "A":
            continue
        measurement_id = description.get("measurementId")
        parameter = parameters_by_measurement.get(measurement_id, {})
        parameter_id = parameter.get("parameterId")
        phase = _phase_name(parameter.get("acMeasuredPhases")) or str(limit_id)
        permitted = permitted_by_parameter.get(parameter_id, {})
        minimum, maximum, default = _range_values(
            permitted.get("permittedValueSet", permitted)
        )
        current = values_by_id.get(limit_id, {})
        result[bucket_name]["supported"] = True
        result[bucket_name]["limits"][phase] = {
            "id": limit_id,
            "measurement_id": measurement_id,
            "parameter_id": parameter_id,
            "active": current.get("isLimitActive"),
            "value_a": scaled_number(current.get("value")),
            "changeable": current.get("isLimitChangeable", True),
            "min_a": minimum,
            "max_a": maximum,
            "default_a": default,
        }
    for bucket in result.values():
        items = list(bucket["limits"].values())
        minima = [item["min_a"] for item in items if item.get("min_a") is not None]
        maxima = [item["max_a"] for item in items if item.get("max_a") is not None]
        defaults = [
            item["default_a"] for item in items if item.get("default_a") is not None
        ]
        active_values = [
            item["value_a"]
            for item in items
            if item.get("active") is True and item.get("value_a") is not None
        ]
        bucket["min_a"] = max(minima) if minima else 6.0
        bucket["max_a"] = min(maxima) if maxima else 32.0
        bucket["default_a"] = min(defaults) if defaults else bucket["max_a"]
        bucket["active"] = any(item.get("active") is True for item in items)
        bucket["value_a"] = min(active_values) if active_values else bucket["max_a"]
    return result
