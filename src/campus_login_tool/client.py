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
        self._last_user_index: str | None = None

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
            response = session.get(self.config.trigger_url, timeout=timeout, allow_redirects=True)
            login_url = self._find_login_page_url(response)
            if login_url:
                return True, sanitize_url(login_url)
            user_index, status = self._discover_user_index_from_response(response)
            if status == "online" and user_index:
                return True, "当前已在线，并发现有效会话。"
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

    def build_interface_request(self, method: str, data: PreparedData) -> PreparedRequest:
        """Build a portal interface request that targets a method under InterFace.do."""
        parsed = urlparse(self.config.trigger_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        endpoint = urljoin(self.config.trigger_url, f"/eportal/InterFace.do?method={method}")
        return PreparedRequest(
            url=endpoint,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": origin,
                "Referer": self.config.trigger_url,
                "Accept": "*/*",
            },
            data=data,
        )

    def _parse_json_response(
        self,
        response: requests.Response,
        *,
        action: str,
    ) -> dict | None:
        content = self._decode_response(response)
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            self.logger.error("解析%s响应失败: %s", action, exc)
            self.logger.debug("%s响应片段: %s", action, truncate_text(content))
            return None

        if isinstance(payload, dict):
            return payload

        self.logger.error("%s响应不是 JSON 对象。", action)
        self.logger.debug("%s响应内容: %s", action, truncate_text(str(payload)))
        return None

    def _post_interface_json(
        self,
        method: str,
        data: PreparedData,
        *,
        timeout: int = 15,
    ) -> dict | None:
        if self.session is None:
            raise RuntimeError("portal 请求前尚未初始化 session。")

        request = self.build_interface_request(method, data)
        try:
            response = self.session.post(
                request.url,
                headers=request.headers,
                data=request.data,
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            self.logger.error("请求 %s 失败: %s", method, exc)
            return None

        payload = self._parse_json_response(response, action=method)
        if payload is not None:
            self.logger.debug("%s 响应字段: %s", method, ", ".join(sorted(payload.keys())))
        return payload

    @staticmethod
    def _looks_like_not_online(message: str) -> bool:
        lowered = message.lower()
        patterns = ("未在线", "不在线", "已下线", "不存在", "not online", "offline")
        return any(pattern in lowered or pattern in message for pattern in patterns)

    @staticmethod
    def _looks_like_incomplete_user_info(message: str) -> bool:
        patterns = ("用户信息不完整", "信息不完整", "please retry")
        lowered = message.lower()
        return any(pattern in message or pattern in lowered for pattern in patterns)

    @staticmethod
    def _extract_user_index_from_url(url: str) -> str | None:
        parsed = urlparse(url)
        return parse_qs(parsed.query).get("userIndex", [None])[0]

    @staticmethod
    def _extract_user_index_from_content(content: str) -> str | None:
        patterns = (
            r"userIndex=([0-9a-fA-F]+)",
            r'"userIndex"\s*:\s*"([0-9a-fA-F]+)"',
            r"userIndex\s*[:=]\s*['\"]?([0-9a-fA-F]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _looks_like_login_page_url(url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.path.endswith("/eportal/index.jsp"):
            return False
        query = parse_qs(parsed.query)
        return any(key in query for key in ("wlanuserip", "nasip", "mac"))

    def _iter_response_urls_and_content(
        self,
        response: requests.Response,
    ) -> tuple[list[str], list[str]]:
        urls: list[str] = []
        contents: list[str] = []
        for item in [*response.history, response]:
            if item.url:
                urls.append(item.url)

            location = item.headers.get("Location")
            if location:
                urls.append(urljoin(item.url or self.config.trigger_url, location))

            content = self._decode_response(item)
            contents.append(content)

            redirect_url = self._extract_redirect_url(content)
            if redirect_url:
                urls.append(urljoin(item.url or self.config.trigger_url, redirect_url))

        return urls, contents

    def _find_login_page_url(self, response: requests.Response) -> str | None:
        urls, _ = self._iter_response_urls_and_content(response)
        for url in urls:
            if self._looks_like_login_page_url(url):
                return url
        return None

    def _discover_user_index_from_response(
        self,
        response: requests.Response,
    ) -> tuple[str | None, str]:
        urls, contents = self._iter_response_urls_and_content(response)

        for url in urls:
            user_index = self._extract_user_index_from_url(url)
            if user_index:
                return user_index, "online"

        for content in contents:
            user_index = self._extract_user_index_from_content(content)
            if user_index:
                return user_index, "online"

        for url in urls:
            if self._looks_like_login_page_url(url):
                return None, "not_online"

        return None, "error"

    def _discover_user_index(self) -> tuple[str | None, str]:
        self.logger.info("正在发现当前在线会话的 userIndex")
        try:
            response = self.session.get(self.config.trigger_url, timeout=15, allow_redirects=True)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            self.logger.error("访问认证入口失败: %s", exc)
            return None, "error"

        user_index, status = self._discover_user_index_from_response(response)
        if status == "online" and user_index:
            self.logger.info("已从认证入口发现 userIndex")
            return user_index, status
        if status == "not_online":
            self.logger.info("认证入口当前返回登录页，说明当前未在线。")
            return None, status

        self.logger.error("无法从认证入口响应中提取 userIndex。")
        urls, _ = self._iter_response_urls_and_content(response)
        if urls:
            self.logger.debug("认证入口候选 URL: %s", " | ".join(sanitize_url(url) for url in urls))
        return None, status

    def _get_login_page_url(self) -> str | None:
        self.logger.info("正在访问认证触发地址")
        self.logger.debug("认证触发地址: %s", self.config.trigger_url)

        try:
            response = self.session.get(self.config.trigger_url, timeout=15, allow_redirects=True)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            self.logger.error("访问认证触发地址失败: %s", exc)
            return None

        login_url = self._find_login_page_url(response)
        if login_url:
            self.logger.info("已发现登录页面")
            self.logger.debug("登录页面 URL: %s", sanitize_url(login_url))
            return login_url

        self.logger.error("无法从认证触发响应中提取登录页面 URL。")
        self.logger.debug("触发入口最终 URL: %s", sanitize_url(response.url))
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
        success, message = self._parse_login_response(content)
        if success:
            payload = self._parse_json_response(response, action="login")
            if payload is not None:
                self._last_user_index = payload.get("userIndex") or self._last_user_index
        return success, message

    def get_online_user_info(self, user_index: str) -> dict | None:
        """Fetch online session information for a userIndex."""
        return self._post_interface_json(
            "getOnlineUserInfo",
            {"userIndex": user_index},
        )

    def _get_online_user_info_with_retry(
        self,
        initial_user_index: str,
    ) -> tuple[dict | None, str]:
        user_index = initial_user_index
        for attempt in range(2):
            info = self.get_online_user_info(user_index)
            if info is None:
                return None, user_index

            if info.get("result") == "success":
                return info, user_index

            message = str(info.get("message") or "获取在线状态失败")
            if not self._looks_like_incomplete_user_info(message) or attempt == 1:
                return info, user_index

            self.logger.info("在线状态信息暂不完整，等待后重试一次。")
            self._sleep(1)
            rediscovered_user_index, discovery_status = self._discover_user_index()
            if discovery_status == "not_online":
                return {"result": "fail", "message": "当前用户不在线"}, user_index
            if rediscovered_user_index:
                user_index = rediscovered_user_index

        return None, user_index

    def logout(self, user_index: str | None = None) -> bool:
        """Log out the current campus network session if one is active."""
        self.logger.info("开始校园网退出登录")
        self.session = self._create_session()
        try:
            resolved_user_index = user_index or self._last_user_index
            discovery_status = "online"
            if not resolved_user_index:
                resolved_user_index, discovery_status = self._discover_user_index()

            if discovery_status == "not_online":
                self.logger.info("当前未检测到在线会话，无需退出。")
                return True

            if not resolved_user_index:
                self.logger.error(
                    "无法确定 userIndex。"
                    "请在已在线状态下运行，或手动提供 --user-index。"
                )
                return False

            info, resolved_user_index = self._get_online_user_info_with_retry(resolved_user_index)
            if info is None:
                return False

            if info.get("result") != "success":
                message = str(info.get("message") or "获取在线状态失败")
                if self._looks_like_not_online(message):
                    self.logger.info("当前未检测到在线会话，无需退出。")
                    return True
                self.logger.error("获取在线状态失败: %s", message)
                return False

            self.logger.info("检测到在线会话，正在提交退出请求。")
            payload = self._post_interface_json(
                "logout",
                {"userIndex": resolved_user_index},
            )
            if payload is None:
                return False

            if payload.get("result") == "success":
                message = str(payload.get("message") or "下线成功")
                self.logger.info(message)
                self._last_user_index = None
                return True

            message = str(payload.get("message") or "退出登录失败")
            if self._looks_like_not_online(message):
                self.logger.info("当前未检测到在线会话，无需退出。")
                return True

            self.logger.error("退出登录失败: %s", message)
            return False
        except Exception as exc:  # pragma: no cover - safety net
            self.logger.exception("退出登录过程发生未处理异常: %s", exc)
            return False
        finally:
            if self.session is not None:
                self.session.close()
                self.session = None

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
