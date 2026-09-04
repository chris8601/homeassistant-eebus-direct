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


if __name__ == "__main__":
    unittest.main()
