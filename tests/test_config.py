"""Tests for configuration precedence and safety."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campus_login_tool.config import (
    ConfigError,
    determine_config_path,
    resolve_config,
    write_default_config,
)


class ConfigTests(unittest.TestCase):
    def test_cli_values_override_env_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            config_path.write_text(
                "[credentials]\n"
                "username = file_user\n"
                "password = file_pass\n"
                "\n[settings]\n"
                "check_url = https://file.example\n"
                "check_interval = 10\n"
                "max_retries = 2\n",
                encoding="utf-8",
            )
            config_path.chmod(0o600)

            with patch.dict(
                os.environ,
                {
                    "CAMPUS_LOGIN_USERNAME": "env_user",
                    "CAMPUS_LOGIN_PASSWORD": "env_pass",
                    "CAMPUS_LOGIN_CHECK_URL": "https://env.example",
                    "CAMPUS_LOGIN_INTERVAL": "20",
                    "CAMPUS_LOGIN_RETRIES": "4",
                },
                clear=False,
            ):
                resolved = resolve_config(
                    cli_values={
                        "username": "cli_user",
                        "password": "cli_pass",
                        "check_url": "https://cli.example",
                        "check_interval": 30,
                        "max_retries": 8,
                    },
                    config_path=config_path,
                )

        self.assertEqual(resolved.username, "cli_user")
        self.assertEqual(resolved.password, "cli_pass")
        self.assertEqual(resolved.check_url, "https://cli.example")
        self.assertEqual(resolved.check_interval, 30)
        self.assertEqual(resolved.max_retries, 8)
        self.assertEqual(resolved.username_source, "cli")
        self.assertEqual(resolved.password_source, "cli")

    def test_env_values_override_config_when_cli_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            config_path.write_text(
                "[credentials]\n"
                "username = file_user\n"
                "password = file_pass\n",
                encoding="utf-8",
            )
            config_path.chmod(0o600)

            with patch.dict(
                os.environ,
                {
                    "CAMPUS_LOGIN_USERNAME": "env_user",
                    "CAMPUS_LOGIN_PASSWORD": "env_pass",
                },
                clear=False,
            ):
                resolved = resolve_config(cli_values={}, config_path=config_path)

        self.assertEqual(resolved.username, "env_user")
        self.assertEqual(resolved.password, "env_pass")
        self.assertEqual(resolved.username_source, "env")
        self.assertEqual(resolved.password_source, "env")

    @unittest.skipIf(os.name == "nt", "POSIX file permission checks are not portable to Windows")
    def test_password_file_requires_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            config_path.write_text(
                "[credentials]\nusername = user\npassword = secret\n",
                encoding="utf-8",
            )
            config_path.chmod(0o644)

            with self.assertRaises(ConfigError):
                resolve_config(cli_values={}, config_path=config_path)

    @unittest.skipIf(os.name == "nt", "POSIX file permission checks are not portable to Windows")
    def test_cli_password_does_not_fail_on_insecure_unused_config_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            config_path.write_text(
                "[credentials]\nusername = file_user\npassword = file_secret\n",
                encoding="utf-8",
            )
            config_path.chmod(0o644)

            resolved = resolve_config(
                cli_values={"username": "cli_user", "password": "cli_secret"},
                config_path=config_path,
            )

        self.assertEqual(resolved.username, "cli_user")
        self.assertEqual(resolved.password, "cli_secret")

    def test_missing_credentials_raise_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            config_path.write_text("[settings]\ncheck_interval = 30\n", encoding="utf-8")

            with self.assertRaises(ConfigError):
                resolve_config(cli_values={}, config_path=config_path)

    def test_write_default_config_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            created = write_default_config(config_path)

            self.assertEqual(created, config_path)
            self.assertTrue(config_path.exists())

    def test_determine_config_path_prefers_cli(self) -> None:
        with patch.dict(os.environ, {"CAMPUS_LOGIN_CONFIG": "/tmp/from-env.conf"}, clear=False):
            resolved = determine_config_path("/tmp/from-cli.conf")

        self.assertEqual(str(resolved), "/tmp/from-cli.conf")
