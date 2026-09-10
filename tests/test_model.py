"""Unit tests for device-independent EEBUS decoding."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "eebus" / "model.py"
SPEC = importlib.util.spec_from_file_location("eebus_model", MODULE_PATH)
assert SPEC and SPEC.loader
model = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model)


class ModelTests(unittest.TestCase):
    def test_scaled_number(self) -> None:
        self.assertEqual(model.scaled_number({"number": 163, "scale": -1}), 16.3)
        self.assertEqual(
            model.scaled_number({"scaledNumber": {"number": 11, "scale": 3}}), 11000
        )

    def test_feature_lookup_tracks_dynamic_ev_entity(self) -> None:
        discovery = {
            "entityInformation": [
                {
                    "description": {
                        "entityAddress": {"entity": [1]},
                        "entityType": "EVSE",
                    }
                },
                {
                    "description": {
                        "entityAddress": {"entity": [1, 1]},
                        "entityType": "EV",
                    }
                },
            ],
            "featureInformation": [
                {
                    "description": {
                        "featureAddress": {"entity": [1, 1], "feature": 10},
                        "featureType": "LoadControl",
                        "role": "server",
                        "supportedFunction": [
                            {
                                "function": "loadControlLimitListData",
                                "possibleOperations": {"write": {}},
                            }
                        ],
                    }
                }
            ],
        }
        self.assertEqual(model.preferred_ev_entity(discovery), [1, 1])
        found = model.find_features(
            discovery,
            "LoadControl",
            entity=[1, 1],
            function_name="loadControlLimitListData",
            default_device="d:_i:HAGER",
        )
        self.assertEqual(found[0]["address"]["device"], "d:_i:HAGER")

    def test_partial_read_support_is_detected_from_witty_discovery(self) -> None:
        feature = {
            "functions": [
                {
                    "function": "measurementListData",
                    "possibleOperations": {"read": {"partial": []}},
                },
                {
                    "function": "otherData",
                    "possibleOperations": {"read": []},
                },
            ]
        }
        self.assertTrue(
            model.feature_supports_partial_read(feature, "measurementListData")
        )
        self.assertFalse(model.feature_supports_partial_read(feature, "otherData"))

    def test_measurement_ids_are_mapped_by_descriptions(self) -> None:
        descriptions = {
            "measurementDescriptionData": [
                {
                    "measurementId": 70,
                    "measurementType": "power",
                    "unit": "W",
                    "scopeType": "acPowerTotal",
                },
                {
                    "measurementId": 71,
                    "measurementType": "current",
                    "unit": "A",
                    "scopeType": "acCurrent",
                },
                {
                    "measurementId": 72,
                    "measurementType": "energy",
                    "unit": "Wh",
                    "scopeType": "charge",
                },
                {
                    "measurementId": 73,
                    "measurementType": "percentage",
                    "unit": "%",
                    "scopeType": "stateOfCharge",
                },
            ]
        }
        parameters = {
            "electricalConnectionParameterDescriptionData": [
                {"parameterId": 1, "measurementId": 71, "acMeasuredPhases": "a"}
            ]
        }
        values = {
            "measurementData": [
                {"measurementId": 70, "value": {"number": 7360, "scale": 0}},
                {"measurementId": 71, "value": {"number": 106, "scale": -1}},
                {"measurementId": 72, "value": {"number": 1254, "scale": 0}},
                {"measurementId": 73, "value": {"number": 82, "scale": 0}},
            ]
        }
        decoded = model.decode_measurements(descriptions, parameters, values)
        self.assertEqual(decoded["power_w"], 7360)
        self.assertAlmostEqual(decoded["current_a_a"], 10.6)
        self.assertEqual(decoded["session_energy_kwh"], 1.254)
        self.assertEqual(decoded["state_of_charge_pct"], 82)

    def test_opev_and_oscev_limits_are_not_mixed(self) -> None:
        descriptions = {
            "loadControlLimitDescriptionData": [
                {
                    "limitId": 1,
                    "limitType": "maxValueLimit",
                    "limitCategory": "obligation",
                    "measurementId": 1,
                    "unit": "A",
                    "scopeType": "overloadProtection",
                },
                {
                    "limitId": 11,
                    "limitType": "maxValueLimit",
                    "limitCategory": "recommendation",
                    "measurementId": 1,
                    "unit": "A",
                    "scopeType": "selfConsumption",
                },
            ]
        }
        parameters = {
            "electricalConnectionParameterDescriptionData": [
                {"parameterId": 1, "measurementId": 1, "acMeasuredPhases": "a"}
            ]
        }
        permitted = {
            "electricalConnectionPermittedValueSetData": [
                {
                    "parameterId": 1,
                    "permittedValueSet": [
                        {
                            "range": [
                                {
                                    "min": {"number": 60, "scale": -1},
                                    "max": {"number": 320, "scale": -1},
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        values = {
            "loadControlLimitData": [
                {
                    "limitId": 1,
                    "isLimitActive": True,
                    "isLimitChangeable": True,
                    "value": {"number": 100, "scale": -1},
                },
                {
                    "limitId": 11,
                    "isLimitActive": True,
                    "isLimitChangeable": True,
                    "value": {"number": 80, "scale": -1},
                },
            ]
        }
        decoded = model.decode_limits(descriptions, values, parameters, permitted)
        self.assertEqual(decoded["obligation"]["value_a"], 10)
        self.assertEqual(decoded["recommendation"]["value_a"], 8)
        self.assertEqual(decoded["obligation"]["min_a"], 6)
        self.assertEqual(decoded["obligation"]["max_a"], 32)

    def test_charging_activity_requires_a_real_measurement(self) -> None:
        self.assertIsNone(model.charging_activity({"raw": []}))
        self.assertFalse(
            model.charging_activity({"raw": [], "power_w": 0, "current_a": 0})
        )
        self.assertTrue(
            model.charging_activity({"raw": [], "power_w": 3680, "current_a": 16})
        )

    def test_status_is_unknown_when_connected_without_charge_samples(self) -> None:
        self.assertIsNone(
            model.charging_status(
                fault=False, vehicle_connected=True, charging=None
            )
        )
        self.assertEqual(
            model.charging_status(
                fault=False, vehicle_connected=True, charging=True
            ),
            "charging",
        )
        self.assertEqual(
            model.charging_status(
                fault=True, vehicle_connected=True, charging=None
            ),
            "fault",
        )


if __name__ == "__main__":
    unittest.main()
