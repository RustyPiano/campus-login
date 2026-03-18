"""CLI smoke tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from campus_login_tool.cli import legacy_main, main


class CliTests(unittest.TestCase):
    def test_main_help_exits_cleanly(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["--help"])

        self.assertEqual(ctx.exception.code, 0)

    def test_init_config_creates_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "campus.conf"
            exit_code = main(["init-config", "--config", str(path)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(path.exists())

    def test_doctor_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            config_path.write_text(
                "[credentials]\nusername = user\npassword = secret\n",
                encoding="utf-8",
            )
            config_path.chmod(0o600)

            with patch("campus_login_tool.cli.CampusLoginClient.check_internet", return_value=True), patch(
                "campus_login_tool.cli.CampusLoginClient.probe_auth_trigger",
                return_value=(True, "http://172.16.128.139/eportal/index.jsp"),
            ):
                exit_code = main(["doctor", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)

    def test_watch_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"
            config_path.write_text(
                "[credentials]\nusername = user\npassword = secret\n",
                encoding="utf-8",
            )
            config_path.chmod(0o600)

            with patch("campus_login_tool.cli.CampusLoginClient.check_internet", return_value=True), patch(
                "campus_login_tool.cli.WatchRunner.run",
                return_value=None,
            ) as run_mock:
                exit_code = main(["watch", "--config", str(config_path)])

        self.assertEqual(exit_code, 0)
        run_mock.assert_called_once()

    def test_legacy_mode_warns_about_deprecated_flags(self) -> None:
        mock_logger = Mock()
        with patch("campus_login_tool.cli.setup_logging", return_value=mock_logger), patch(
            "campus_login_tool.cli.WatchRunner.run",
            return_value=None,
        ):
            exit_code = legacy_main(
                ["-u", "user", "-p", "secret", "--daemon", "--force"]
            )

        self.assertEqual(exit_code, 0)
        warning_messages = "\n".join(call.args[0] for call in mock_logger.warning.call_args_list)
        self.assertIn("兼容模式：`python campus_login.py` 已弃用", warning_messages)
        self.assertIn("`--password` 已弃用", warning_messages)
        self.assertIn("`--daemon` 已弃用", warning_messages)

    def test_password_stdin_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "campus.conf"

            with patch("campus_login_tool.cli.CampusLoginClient.login_with_retry", return_value=True):
                with patch("sys.stdin", io.StringIO("secret\n")):
                    exit_code = main(
                        [
                            "login",
                            "--config",
                            str(config_path),
                            "--username",
                            "user",
                            "--password-stdin",
                            "--force",
                        ]
                    )

        self.assertEqual(exit_code, 0)
