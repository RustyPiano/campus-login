"""Configuration loading and validation."""

from __future__ import annotations

import os
import stat
from configparser import ConfigParser, Error as ConfigParserError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

DEFAULT_TRIGGER_URL = "http://123.123.123.123"
DEFAULT_INTERNET_CHECK_URL = "https://www.baidu.com"
DEFAULT_CHECK_INTERVAL = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_CONFIG_PATH = Path.home() / ".campus_login.conf"

ENV_CONFIG_PATH = "CAMPUS_LOGIN_CONFIG"
ENV_USERNAME = "CAMPUS_LOGIN_USERNAME"
ENV_PASSWORD = "CAMPUS_LOGIN_PASSWORD"
ENV_CHECK_URL = "CAMPUS_LOGIN_CHECK_URL"
ENV_INTERVAL = "CAMPUS_LOGIN_INTERVAL"
ENV_RETRIES = "CAMPUS_LOGIN_RETRIES"

CONFIG_TEMPLATE = """# 校园网自动登录配置文件
# 推荐用 `campus-login init-config` 生成本文件。
# 凭证以明文保存，仅建议在可信设备上使用，并将权限限制为 600。

[credentials]
# username = your_username
# password = your_password

[settings]
# 用于检测是否已联网；默认值通常够用
check_url = https://www.baidu.com

# watch 模式下的检测间隔（秒）
check_interval = 30

# 登录失败时的最大重试次数
max_retries = 3
"""


class ConfigError(ValueError):
    """Raised when configuration is invalid or unsafe."""


@dataclass(slots=True)
class ResolvedConfig:
    """Fully resolved runtime configuration."""

    username: str
    password: str
    check_url: str
    check_interval: int
    max_retries: int
    trigger_url: str
    config_path: Path
    username_source: str
    password_source: str
    check_url_source: str
    interval_source: str
    retries_source: str

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)


def determine_config_path(cli_path: Optional[str]) -> Path:
    """Resolve the configuration path with CLI > env > default precedence."""
    raw_path = cli_path or os.getenv(ENV_CONFIG_PATH)
    return Path(raw_path).expanduser() if raw_path else DEFAULT_CONFIG_PATH


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    parser = ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except ConfigParserError as exc:
        raise ConfigError(f"无法解析配置文件 {path}: {exc}") from exc

    credentials = parser["credentials"] if parser.has_section("credentials") else {}
    settings = parser["settings"] if parser.has_section("settings") else {}

    password = _normalize_value(credentials.get("password"))

    return {
        "username": _normalize_value(credentials.get("username")),
        "password": password,
        "check_url": _normalize_value(settings.get("check_url")),
        "check_interval": _parse_optional_int(settings.get("check_interval"), "配置文件中的 check_interval"),
        "max_retries": _parse_optional_int(settings.get("max_retries"), "配置文件中的 max_retries"),
    }


def _read_env() -> dict[str, Any]:
    return {
        "username": _normalize_value(os.getenv(ENV_USERNAME)),
        "password": _normalize_value(os.getenv(ENV_PASSWORD)),
        "check_url": _normalize_value(os.getenv(ENV_CHECK_URL)),
        "check_interval": _parse_optional_int(os.getenv(ENV_INTERVAL), ENV_INTERVAL),
        "max_retries": _parse_optional_int(os.getenv(ENV_RETRIES), ENV_RETRIES),
    }


def resolve_config(
    *,
    cli_values: Mapping[str, Any],
    config_path: Path,
    require_credentials: bool = True,
) -> ResolvedConfig:
    """Merge CLI, environment, config file, and defaults into a runtime config."""
    file_values = _read_config_file(config_path)
    env_values = _read_env()

    username, username_source = _first_defined(
        (cli_values.get("username"), "cli"),
        (env_values["username"], "env"),
        (file_values.get("username"), "config"),
        (None, "missing"),
    )
    password, password_source = _first_defined(
        (cli_values.get("password"), "cli"),
        (env_values["password"], "env"),
        (file_values.get("password"), "config"),
        (None, "missing"),
    )
    check_url, check_url_source = _first_defined(
        (cli_values.get("check_url"), "cli"),
        (env_values["check_url"], "env"),
        (file_values.get("check_url"), "config"),
        (DEFAULT_INTERNET_CHECK_URL, "default"),
    )
    check_interval, interval_source = _first_defined(
        (cli_values.get("check_interval"), "cli"),
        (env_values["check_interval"], "env"),
        (file_values.get("check_interval"), "config"),
        (DEFAULT_CHECK_INTERVAL, "default"),
    )
    max_retries, retries_source = _first_defined(
        (cli_values.get("max_retries"), "cli"),
        (env_values["max_retries"], "env"),
        (file_values.get("max_retries"), "config"),
        (DEFAULT_MAX_RETRIES, "default"),
    )

    if check_interval is None or check_interval <= 0:
        raise ConfigError("检测间隔必须是正整数。")
    if max_retries is None or max_retries <= 0:
        raise ConfigError("最大重试次数必须是正整数。")

    username = username or ""
    password = password or ""

    if require_credentials and not username:
        raise ConfigError("未找到校园网用户名。请使用 --username、环境变量或配置文件提供。")
    if require_credentials and not password:
        raise ConfigError(
            "未找到校园网密码。请通过交互输入、--password-stdin、环境变量或配置文件提供。"
        )

    if password_source == "config":
        ensure_secure_config_permissions(config_path)

    return ResolvedConfig(
        username=username,
        password=password,
        check_url=check_url,
        check_interval=check_interval,
        max_retries=max_retries,
        trigger_url=DEFAULT_TRIGGER_URL,
        config_path=config_path,
        username_source=username_source,
        password_source=password_source,
        check_url_source=check_url_source,
        interval_source=interval_source,
        retries_source=retries_source,
    )


def write_default_config(path: Path, force: bool = False) -> Path:
    """Create a starter configuration file with secure permissions."""
    path = path.expanduser()
    if path.exists() and not force:
        raise ConfigError(f"配置文件已存在: {path}。如需覆盖，请使用 --force。")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def ensure_secure_config_permissions(path: Path) -> None:
    """Reject password-bearing config files that are readable by others."""
    if os.name == "nt":
        return

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigError(
            f"配置文件权限过宽 ({oct(mode)})：{path}。如果文件中保存了密码，请先执行 `chmod 600 {path}`。"
        )


def describe_credential_source(resolved: ResolvedConfig) -> str:
    """Return a user-facing description of where credentials came from."""
    if resolved.password_source == "cli":
        return "CLI"
    if resolved.password_source == "env":
        return "环境变量"
    if resolved.password_source == "config":
        return f"配置文件 ({resolved.config_path})"
    return "未配置"


def _parse_optional_int(raw_value: Optional[str], label: str) -> Optional[int]:
    value = _normalize_value(raw_value)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} 必须是整数，当前值为 {value!r}。") from exc


def _first_defined(*candidates: Tuple[Any, str]) -> Tuple[Any, str]:
    for value, source in candidates:
        if value is None:
            continue
        return value, source
    return None, "missing"


def _normalize_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
