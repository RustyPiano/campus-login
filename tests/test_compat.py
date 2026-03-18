"""Compatibility and documentation regression tests."""

import importlib
import unittest
from pathlib import Path

from campus_login_tool import security as package_security
from campus_login_tool.config import CONFIG_TEMPLATE

ROOT = Path(__file__).resolve().parent.parent


class CompatibilityTests(unittest.TestCase):
    def test_example_config_matches_generated_template(self) -> None:
        example_path = ROOT / "campus_login.conf.example"

        self.assertEqual(example_path.read_text(encoding="utf-8"), CONFIG_TEMPLATE)

    def test_root_security_module_re_exports_package_helpers(self) -> None:
        legacy_security = importlib.import_module("security")

        self.assertIs(legacy_security.encryptPassword, package_security.encryptPassword)
        self.assertEqual(legacy_security.biRadix, package_security.biRadix)
