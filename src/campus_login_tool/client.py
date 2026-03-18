"""Core network client for the Eportal login flow."""

import gzip
import io
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests

from .config import ResolvedConfig
from .logging_utils import mask_value, sanitize_url, truncate_text
from .security import encryptPassword

PreparedData = dict[str, str]
SleepFunc = Callable[[float], None]


@dataclass(slots=True)
class PreparedRequest:
    """Request information assembled before sending."""

    url: str
    headers: dict[str, str]
    data: PreparedData


class CampusLoginClient:
    """Campus network login client."""

    def __init__(
        self,
        config: ResolvedConfig,
        logger: logging.Logger,
        *,
        session_factory: Callable[[], requests.Session] = requests.Session,
        request_get: Callable[..., requests.Response] = requests.get,
        sleep_func: SleepFunc = time.sleep,
    ) -> None:
        self.config = config
        self.logger = logger
        self.session: requests.Session | None = None
        self._session_factory = session_factory
        self._request_get = request_get
        self._sleep = sleep_func

    def _create_session(self) -> requests.Session:
        session = self._session_factory()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        return session

    def _decode_response(self, response: requests.Response) -> str:
        """Decode response bodies that may be compressed or use GBK encodings."""
        content_encoding = response.headers.get("Content-Encoding", "").lower()
        payload = response.content

        if content_encoding == "gzip":
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as handle:
                    payload = handle.read()
            except OSError as exc:
                self.logger.warning("gzip 解压失败，继续尝试按原始内容解析: %s", exc)

        for encoding in ("utf-8", "gb18030", "gbk"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue

        return payload.decode("utf-8", errors="replace")

    @staticmethod
    def _extract_redirect_url(content: str) -> str | None:
        patterns = (
            r"top\.self\.location\.href='(.*?)'",
            r'top\.self\.location\.href="(.*?)"',
            r"location\.href\s*=\s*'(.*?)'",
            r'location\.href\s*=\s*"(.*?)"',
        )
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _parse_login_response(content: str) -> tuple[bool, str]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            lowered = content.lower()
            if "success" in lowered or "成功" in content:
                return True, "登录成功（通过响应文本识别）"
            detail = truncate_text(content, 80)
            return False, f"无法识别登录响应，请使用 --verbose 查看详情。响应片段: {detail}"

        if payload.get("result") == "success":
            message = payload.get("message") or "登录成功"
            return True, message

        message = payload.get("message") or "登录失败"
        return False, message

    def check_internet(self, timeout: int = 5) -> bool:
        """Check whether the configured probe URL is reachable."""
        try:
            response = self._request_get(
                self.config.check_url,
                timeout=timeout,
                allow_redirects=True,
            )
        except requests.exceptions.Timeout:
            self.logger.debug("网络检测超时: %s", self.config.check_url)
            return False
        except requests.exceptions.RequestException as exc:
            self.logger.debug("网络检测失败: %s", exc)
            return False

        if 200 <= response.status_code < 300 and response.content:
            self.logger.debug("网络检测成功: %s", self.config.check_url)
            return True

        self.logger.debug("网络检测返回异常状态: %s", response.status_code)
        return False

    def probe_auth_trigger(self, timeout: int = 10) -> tuple[bool, str]:
        """Probe the trigger endpoint and report whether a login page is discoverable."""
        session = self._create_session()
        try:
            response = session.get(self.config.trigger_url, timeout=timeout, allow_redirects=False)
            content = self._decode_response(response)
            login_url = self._extract_redirect_url(content)
            if login_url:
                return True, sanitize_url(login_url)
            return False, f"认证触发地址返回了 HTTP {response.status_code}，但未找到登录页跳转。"
        except requests.exceptions.RequestException as exc:
            return False, f"无法访问认证触发地址: {exc}"
        finally:
            session.close()

    def build_page_info_request(self, login_page_url: str, query_string: str) -> PreparedRequest:
        parsed = urlparse(login_page_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return PreparedRequest(
            url=urljoin(login_page_url, "/eportal/InterFace.do?method=pageInfo"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": login_page_url,
                "Origin": origin,
                "Accept": "*/*",
            },
            data={"queryString": quote(query_string)},
        )

    def build_login_request(
        self,
        login_page_url: str,
        query_string: str,
        encrypted_password: str,
        service: str = "",
    ) -> PreparedRequest:
        parsed = urlparse(login_page_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return PreparedRequest(
            url=urljoin(login_page_url, "/eportal/InterFace.do?method=login"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": login_page_url,
                "Origin": origin,
            },
            data={
                "userId": self.config.username,
                "password": encrypted_password,
                "service": service,
                "queryString": quote(query_string),
                "operatorPwd": "",
                "operatorUserId": "",
                "validcode": "",
                "passwordEncrypt": "true",
            },
        )

    def _get_login_page_url(self) -> str | None:
        self.logger.info("正在访问认证触发地址")
        self.logger.debug("认证触发地址: %s", self.config.trigger_url)

        try:
            response = self.session.get(self.config.trigger_url, timeout=15, allow_redirects=False)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            self.logger.error("访问认证触发地址失败: %s", exc)
            return None

        content = self._decode_response(response)
        login_url = self._extract_redirect_url(content)
        if login_url:
            self.logger.info("已发现登录页面")
            self.logger.debug("登录页面 URL: %s", sanitize_url(login_url))
            return login_url

        self.logger.error("无法从认证触发响应中提取登录页面 URL。")
        self.logger.debug("触发响应片段: %s", truncate_text(content))
        return None

    def _get_page_info(self, login_page_url: str, query_string: str) -> dict | None:
        request = self.build_page_info_request(login_page_url, query_string)
        self.logger.info("正在获取 pageInfo 公钥信息")
        try:
            response = self.session.post(
                request.url,
                headers=request.headers,
                data=request.data,
                timeout=15,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            self.logger.error("请求 pageInfo 失败: %s", exc)
            return None

        try:
            payload = response.json()
        except json.JSONDecodeError:
            content = self._decode_response(response)
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                self.logger.error("解析 pageInfo 响应失败: %s", exc)
                self.logger.debug("pageInfo 响应片段: %s", truncate_text(content))
                return None

        self.logger.debug("pageInfo 字段: %s", ", ".join(sorted(payload.keys())))
        return payload

    def _submit_login(
        self,
        login_page_url: str,
        query_string: str,
        encrypted_password: str,
        service: str = "",
    ) -> tuple[bool, str]:
        request = self.build_login_request(
            login_page_url,
            query_string,
            encrypted_password,
            service,
        )
        self.logger.info("正在提交登录请求")

        try:
            response = self.session.post(
                request.url,
                headers=request.headers,
                data=request.data,
                timeout=20,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            return False, f"登录请求失败: {exc}"

        content = self._decode_response(response)
        self.logger.debug("登录响应片段: %s", truncate_text(content))
        return self._parse_login_response(content)

    def login(self) -> bool:
        """Execute the full login flow."""
        self.logger.info("开始校园网登录")
        self.logger.debug("用户名: %s", mask_value(self.config.username))

        self.session = self._create_session()
        try:
            login_page_url = self._get_login_page_url()
            if not login_page_url:
                return False

            parsed = urlparse(login_page_url)
            query_params = parse_qs(parsed.query)
            query_string = parsed.query
            mac_address = query_params.get("mac", [None])[0]
            service = query_params.get("service", [""])[0]

            if not mac_address:
                self.logger.error("登录页参数缺少 mac，无法继续加密密码。")
                return False

            self.logger.debug("已解析登录页查询串，mac: %s", mask_value(mac_address))

            try:
                self.session.get(login_page_url, timeout=15)
            except requests.exceptions.RequestException as exc:
                self.logger.warning("访问登录页失败，但继续尝试登录: %s", exc)

            page_info = self._get_page_info(login_page_url, query_string)
            if not page_info:
                return False

            public_key_modulus = page_info.get("publicKeyModulus")
            public_key_exponent = page_info.get("publicKeyExponent", "10001")

            if not public_key_modulus:
                self.logger.error("pageInfo 缺少 publicKeyModulus。")
                return False

            self.logger.info("正在加密密码")
            try:
                encrypted_password = encryptPassword(
                    self.config.password,
                    public_key_exponent,
                    public_key_modulus,
                    mac_address,
                )
            except Exception as exc:  # pragma: no cover - delegated RSA logic
                self.logger.error("密码加密失败: %s", exc)
                return False

            if not encrypted_password:
                self.logger.error("密码加密结果为空。")
                return False

            success, message = self._submit_login(
                login_page_url,
                query_string,
                encrypted_password,
                service,
            )
            if success:
                self.logger.info("登录成功")
                if message and message != "登录成功":
                    self.logger.debug("登录消息: %s", message)
                return True

            self.logger.error("登录失败: %s", message)
            return False
        except Exception as exc:  # pragma: no cover - safety net
            self.logger.exception("登录过程发生未处理异常: %s", exc)
            return False
        finally:
            if self.session is not None:
                self.session.close()
                self.session = None

    def login_with_retry(self) -> bool:
        """Retry login with exponential backoff."""
        for attempt in range(1, self.config.max_retries + 1):
            self.logger.info("登录尝试 %s/%s", attempt, self.config.max_retries)
            if self.login():
                self._sleep(2)
                if self.check_internet():
                    self.logger.info("登录后网络验证成功")
                    return True

                self.logger.warning("登录成功但网络验证尚未通过，稍后再试一次。")
                self._sleep(3)
                if self.check_internet():
                    self.logger.info("延迟复检成功")
                    return True

            if attempt < self.config.max_retries:
                wait_time = 2 ** attempt
                self.logger.info("等待 %s 秒后重试", wait_time)
                self._sleep(wait_time)

        self.logger.error(
            "登录失败，已重试 %s 次。建议使用 `campus-login doctor` 检查配置和网络环境。",
            self.config.max_retries,
        )
        return False
