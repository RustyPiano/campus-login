"""Foreground watch loop for reconnecting the campus network."""

from __future__ import annotations

import logging
import signal
import time
from typing import Callable

from .client import CampusLoginClient


SleepFunc = Callable[[float], None]


class WatchRunner:
    """Continuously monitor network state and reconnect when needed."""

    def __init__(
        self,
        client: CampusLoginClient,
        logger: logging.Logger,
        *,
        sleep_func: SleepFunc = time.sleep,
        install_signal_handlers: bool = True,
    ) -> None:
        self.client = client
        self.logger = logger
        self.running = True
        self._sleep = sleep_func

        if install_signal_handlers:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, _frame) -> None:
        self.logger.info("收到退出信号 %s，正在停止 watch 模式。", signum)
        self.running = False

    def run(self) -> None:
        self.logger.info("watch 模式已启动")
        self.logger.info("检测间隔: %s 秒", self.client.config.check_interval)
        self.logger.info("检测 URL: %s", self.client.config.check_url)

        consecutive_failures = 0
        while self.running:
            try:
                if self.client.check_internet():
                    consecutive_failures = 0
                    self.logger.debug("网络正常")
                else:
                    consecutive_failures += 1
                    self.logger.warning("网络检测失败（连续 %s 次）", consecutive_failures)
                    if consecutive_failures >= 2:
                        self.logger.info("检测到网络断开，尝试重新登录")
                        if self.client.login_with_retry():
                            consecutive_failures = 0
                            self.logger.info("重新登录成功")
                        else:
                            self.logger.error("重新登录失败，将继续监控")

                for _ in range(self.client.config.check_interval):
                    if not self.running:
                        break
                    self._sleep(1)
            except Exception as exc:  # pragma: no cover - protection for long-running mode
                self.logger.exception("watch 模式发生异常: %s", exc)
                self._sleep(10)

        self.logger.info("watch 模式已退出")
