"""Command line interface for the campus login tool."""

import argparse
import sys
from collections.abc import Iterable

from . import __version__
from .client import CampusLoginClient
from .config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    ResolvedConfig,
    describe_credential_source,
    determine_config_path,
    resolve_config,
    write_default_config,
)
from .logging_utils import setup_logging
from .watch import WatchRunner


def build_parser() -> argparse.ArgumentParser:
    """Build the primary CLI parser."""
    parser = argparse.ArgumentParser(
        prog="campus-login",
        description="校园网自动登录工具（Eportal 动态公钥型）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    common.add_argument("--log-file", help="日志文件路径")

    runtime = argparse.ArgumentParser(add_help=False)
    runtime.add_argument("--config", help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}")
    runtime.add_argument("-u", "--username", help="校园网用户名")
    runtime.add_argument(
        "-p",
        "--password",
        help="已弃用：明文密码参数，建议改用交互输入或 --password-stdin",
    )
    runtime.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取密码，适合与密码管理器或管道配合使用",
    )
    runtime.add_argument("--check-url", dest="check_url", help="网络检测 URL")
    runtime.add_argument("--interval", dest="check_interval", type=int, help="检测间隔（秒）")
    runtime.add_argument("--retries", dest="max_retries", type=int, help="最大重试次数")

    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser(
        "login",
        parents=[common, runtime],
        help="执行一次登录",
    )
    login_parser.add_argument("--force", action="store_true", help="跳过登录前的联网检测")
    login_parser.set_defaults(handler=handle_login)

    logout_parser = subparsers.add_parser(
        "logout",
        parents=[common],
        help="退出当前校园网会话",
    )
    logout_parser.add_argument("--config", help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}")
    logout_parser.add_argument("--user-index", help="直接指定 userIndex，跳过本地自动发现")
    logout_parser.set_defaults(handler=handle_logout)

    watch_parser = subparsers.add_parser(
        "watch",
        parents=[common, runtime],
        help="持续监控网络并在断网时自动重连",
    )
    watch_parser.add_argument("--force", action="store_true", help="启动时跳过联网检测")
    watch_parser.set_defaults(handler=handle_watch)

    doctor_parser = subparsers.add_parser(
        "doctor",
        parents=[common, runtime],
        help="检查配置、安全性和当前网络环境",
    )
    doctor_parser.add_argument("--timeout", type=int, default=5, help="网络检测超时（秒）")
    doctor_parser.set_defaults(handler=handle_doctor)

    init_parser = subparsers.add_parser(
        "init-config",
        parents=[common],
        help="生成示例配置文件",
    )
    init_parser.add_argument("--config", help=f"输出路径，默认 {DEFAULT_CONFIG_PATH}")
    init_parser.add_argument("--force", action="store_true", help="覆盖已有配置文件")
    init_parser.set_defaults(handler=handle_init_config)

    return parser


def build_legacy_parser() -> argparse.ArgumentParser:
    """Build the compatibility parser for the historical script interface."""
    parser = argparse.ArgumentParser(
        prog="campus_login.py",
        description="校园网自动登录脚本（兼容模式，建议改用 `campus-login`）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
兼容示例:
  python campus_login.py -u myuser
  python campus_login.py -u myuser --password-stdin
  python campus_login.py --daemon

推荐改用:
  campus-login login
  campus-login watch
""",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-u", "--username", help="校园网用户名")
    parser.add_argument(
        "-p",
        "--password",
        help="已弃用：明文密码参数，建议改用交互输入或 --password-stdin",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取密码，适合与密码管理器或管道配合使用",
    )
    parser.add_argument(
        "-d",
        "--daemon",
        action="store_true",
        help="已弃用：改用 `campus-login watch`",
    )
    parser.add_argument("--config", help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}")
    parser.add_argument("--check-url", dest="check_url", help="网络检测 URL")
    parser.add_argument("-i", "--interval", dest="check_interval", type=int, help="检测间隔（秒）")
    parser.add_argument("-r", "--retries", dest="max_retries", type=int, help="最大重试次数")
    parser.add_argument("--force", action="store_true", help="跳过登录前的联网检测")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--log-file", help="日志文件路径")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """Run the primary CLI."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logger = setup_logging(args.verbose, args.log_file)
    try:
        return int(args.handler(args, logger))
    except ConfigError as exc:
        logger.error(str(exc))
        return 2


def legacy_main(argv: Iterable[str] | None = None) -> int:
    """Run the historical single-command interface."""
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    if argv_list and argv_list[0] in {"login", "logout", "watch", "doctor", "init-config"}:
        return main(argv_list)

    parser = build_legacy_parser()
    args = parser.parse_args(argv_list)
    logger = setup_logging(args.verbose, args.log_file)
    logger.warning("兼容模式：`python campus_login.py` 已弃用，请改用 `campus-login`。")
    if args.daemon:
        logger.warning("兼容模式：`--daemon` 已弃用，请改用 `campus-login watch`。")

    try:
        if args.daemon:
            return handle_watch(args, logger)
        return handle_login(args, logger)
    except ConfigError as exc:
        logger.error(str(exc))
        return 2


def handle_login(args: argparse.Namespace, logger) -> int:
    """Execute a single login attempt."""
    if args.password is not None:
        logger.warning("`--password` 已弃用，建议改用交互输入、环境变量或 `--password-stdin`。")

    config = _resolve_runtime_config(args, require_credentials=True)
    _log_config_summary(config, logger)
    client = CampusLoginClient(config, logger)

    if not args.force:
        logger.info("正在检测网络连通性: %s", config.check_url)
        if client.check_internet():
            logger.info("网络已连接，无需登录。")
            return 0

    return 0 if client.login_with_retry() else 1


def handle_watch(args: argparse.Namespace, logger) -> int:
    """Run the foreground watch loop."""
    if getattr(args, "password", None) is not None:
        logger.warning("`--password` 已弃用，建议改用交互输入、环境变量或 `--password-stdin`。")

    config = _resolve_runtime_config(args, require_credentials=True)
    _log_config_summary(config, logger)
    client = CampusLoginClient(config, logger)

    if not args.force:
        logger.info("正在检测网络连通性: %s", config.check_url)
        if client.check_internet():
            logger.info("网络已连接，watch 模式将继续监控。")
        else:
            logger.info("当前未联网，watch 模式会先尝试登录。")
            client.login_with_retry()

    runner = WatchRunner(client, logger)
    runner.run()
    return 0


def handle_logout(args: argparse.Namespace, logger) -> int:
    """Log out the current online session if one exists."""
    config = _resolve_runtime_config(args, require_credentials=False)
    _log_config_summary(config, logger, include_credentials=False)
    client = CampusLoginClient(config, logger)
    return 0 if client.logout(user_index=getattr(args, "user_index", None)) else 1


def handle_doctor(args: argparse.Namespace, logger) -> int:
    """Run configuration and environment diagnostics."""
    config = _resolve_runtime_config(args, require_credentials=False)
    _log_config_summary(config, logger, include_credentials=False)

    issues = 0
    config_exists = config.config_path.exists()
    if config_exists:
        logger.info("配置文件: %s", config.config_path)
    else:
        logger.info("配置文件未找到: %s", config.config_path)

    if config.username:
        logger.info("用户名来源: %s", config.username_source)
    else:
        logger.warning("未检测到用户名，请配置 --username、环境变量或配置文件。")
        issues += 1

    if config.password:
        logger.info("密码来源: %s", describe_credential_source(config))
    else:
        logger.warning("未检测到密码，请使用交互输入、--password-stdin、环境变量或配置文件。")
        issues += 1

    client = CampusLoginClient(config, logger)
    logger.info("正在检查互联网探测地址: %s", config.check_url)
    if client.check_internet(timeout=args.timeout):
        logger.info("互联网探测通过，当前看起来已经联网。")
    else:
        logger.warning("互联网探测未通过，这通常表示尚未登录，或当前不在校园网环境。")
        issues += 1

    logger.info("正在检查认证触发地址")
    ok, detail = client.probe_auth_trigger(timeout=args.timeout)
    if ok:
        logger.info("认证触发检查通过: %s", detail)
    else:
        logger.warning("认证触发检查失败: %s", detail)
        issues += 1

    if issues == 0:
        logger.info("doctor 检查完成，未发现明显问题。")
        return 0

    logger.warning("doctor 检查完成，发现 %s 个需要关注的问题。", issues)
    return 1


def handle_init_config(args: argparse.Namespace, logger) -> int:
    """Create a starter config file."""
    config_path = determine_config_path(args.config)
    created_path = write_default_config(config_path, force=args.force)
    logger.info("已生成配置文件模板: %s", created_path)
    logger.info("如需保存密码，请确认文件权限保持为 600。")
    return 0


def _resolve_runtime_config(
    args: argparse.Namespace,
    *,
    require_credentials: bool,
) -> ResolvedConfig:
    config_path = determine_config_path(getattr(args, "config", None))
    if getattr(args, "password", None) and getattr(args, "password_stdin", False):
        raise ConfigError("`--password` 和 `--password-stdin` 不能同时使用。")

    password_from_stdin = None
    if getattr(args, "password_stdin", False):
        password_from_stdin = sys.stdin.read().rstrip("\r\n")
        if not password_from_stdin:
            raise ConfigError("`--password-stdin` 已启用，但标准输入中没有读取到密码。")

    cli_values = {
        "username": getattr(args, "username", None),
        "password": (
            password_from_stdin
            if password_from_stdin is not None
            else getattr(args, "password", None)
        ),
        "check_url": getattr(args, "check_url", None),
        "check_interval": getattr(args, "check_interval", None),
        "max_retries": getattr(args, "max_retries", None),
    }

    resolved = resolve_config(
        cli_values=cli_values,
        config_path=config_path,
        require_credentials=False,
    )

    if require_credentials and not resolved.username:
        prompted_username = _read_username_from_tty_if_needed()
        if prompted_username:
            cli_values["username"] = prompted_username

    if require_credentials and not resolved.password and password_from_stdin is None:
        prompted_password = _read_password_from_tty_if_needed()
        if prompted_password:
            cli_values["password"] = prompted_password

    return resolve_config(
        cli_values=cli_values,
        config_path=config_path,
        require_credentials=require_credentials,
    )


def _read_username_from_tty_if_needed() -> str | None:
    if not sys.stdin.isatty():
        return None
    username = input("请输入校园网用户名: ")
    return username.strip() or None


def _read_password_from_tty_if_needed() -> str | None:
    if not sys.stdin.isatty():
        return None

    import getpass

    password = getpass.getpass("请输入校园网密码: ")
    return password.strip() or None


def _log_config_summary(
    config: ResolvedConfig,
    logger,
    *,
    include_credentials: bool = True,
) -> None:
    logger.info("配置文件路径: %s", config.config_path)
    logger.info(
        "配置来源: check_url=%s, interval=%s, retries=%s",
        config.check_url_source,
        config.interval_source,
        config.retries_source,
    )
    if include_credentials:
        logger.info("密码来源: %s", describe_credential_source(config))
