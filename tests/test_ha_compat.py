"""Static compatibility checks for supported Home Assistant releases."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class HomeAssistantCompatibilityTests(unittest.TestCase):
    """Guard imports that would prevent the config flow from loading."""

    def test_config_flow_uses_compatible_options_flow(self) -> None:
        source = ROOT.joinpath("custom_components/eebus/config_flow.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "homeassistant.config_entries"
            for alias in node.names
        }

        self.assertIn("OptionsFlow", imported_names)
        self.assertNotIn("OptionsFlowWithReload", imported_names)

    def test_config_flow_schema_contains_no_custom_ipv4_validator(self) -> None:
        """The probatio UI serializer cannot encode arbitrary callables."""
        source = ROOT.joinpath("custom_components/eebus/config_flow.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("): _validate_interface", source)
        self.assertIn("): str", source)

    def test_pairing_failure_does_not_return_to_pairing_step(self) -> None:
        source = ROOT.joinpath("custom_components/eebus/config_flow.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('async_show_progress_done(next_step_id="pair")', source)
        self.assertIn('async_show_progress_done(next_step_id="pair_failed")', source)
        self.assertIn("async_step_pair_failed", source)


if __name__ == "__main__":
    unittest.main()
