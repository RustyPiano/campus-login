"""Tests for low-level protocol behavior."""

import gzip
import json
import logging
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import requests

from campus_login_tool.client import CampusLoginClient
from campus_login_tool.config import ResolvedConfig

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(
        self,
        *,
        content: bytes,
        status_code: int = 200,
        headers=None,
        json_data=None,
        json_exc=None,
        url: str = "http://172.16.128.139/",
        history=None,
    ):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data
        self._json_exc = json_exc
        self.url = url
        self.history = list(history or [])

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.headers = {}

    def get(self, *args, **kwargs):
        return self.get_responses.pop(0)

    def post(self, *args, **kwargs):
        return self.post_responses.pop(0)

    def close(self) -> None:
        return None


def make_config() -> ResolvedConfig:
    return ResolvedConfig(
        username="test_user",
        password="test_password",
        check_url="https://check.example",
        check_interval=30,
        max_retries=3,
        trigger_url="http://172.16.128.139/",
        config_path=Path("/tmp/campus.conf"),
        username_source="cli",
        password_source="cli",
        check_url_source="default",
        interval_source="default",
        retries_source="default",
    )


def make_logger() -> logging.Logger:
    logger = logging.getLogger("campus_login_test")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


class ClientTests(unittest.TestCase):
    def test_decode_response_supports_gzip_and_gbk(self) -> None:
        text = "登录页面"
        response = FakeResponse(
            content=gzip.compress(text.encode("gb18030")),
            headers={"Content-Encoding": "gzip"},
        )
        client = CampusLoginClient(make_config(), make_logger())

        self.assertEqual(client._decode_response(response), text)

    def test_extract_redirect_url_from_fixture(self) -> None:
        html = (FIXTURES / "redirect.html").read_text(encoding="utf-8")
        redirect_url = CampusLoginClient._extract_redirect_url(html)

        self.assertIsNotNone(redirect_url)
        self.assertIn("/eportal/index.jsp", redirect_url)

    def test_parse_login_response_success_and_failure(self) -> None:
        success, message = CampusLoginClient._parse_login_response(
            '{"result":"success","message":""}'
        )
        failure, fail_message = CampusLoginClient._parse_login_response(
            '{"result":"fail","message":"用户名或密码错误"}'
        )

        self.assertTrue(success)
        self.assertEqual(message, "登录成功")
        self.assertFalse(failure)
        self.assertEqual(fail_message, "用户名或密码错误")

    def test_build_page_info_request_encodes_query_once(self) -> None:
        client = CampusLoginClient(make_config(), make_logger())
        login_url = CampusLoginClient._extract_redirect_url(
            (FIXTURES / "redirect.html").read_text(encoding="utf-8")
        )
        query = urlparse(login_url).query

        prepared = client.build_page_info_request(login_url, query)

        self.assertIn("queryString", prepared.data)
        self.assertIn("%3D", prepared.data["queryString"])
        self.assertEqual(prepared.headers["Origin"], "http://172.16.128.139")

    def test_build_login_request_contains_expected_payload(self) -> None:
        client = CampusLoginClient(make_config(), make_logger())
        login_url = CampusLoginClient._extract_redirect_url(
            (FIXTURES / "redirect.html").read_text(encoding="utf-8")
        )
        query = urlparse(login_url).query

        prepared = client.build_login_request(login_url, query, "encrypted", "campus")

        self.assertEqual(prepared.data["userId"], "test_user")
        self.assertEqual(prepared.data["password"], "encrypted")
        self.assertEqual(prepared.data["service"], "campus")
        self.assertIn("%3D", prepared.data["queryString"])

    def test_build_interface_request_targets_portal_method(self) -> None:
        client = CampusLoginClient(make_config(), make_logger())

        prepared = client.build_interface_request("logout", {"userIndex": "abc"})

        self.assertEqual(prepared.url, "http://172.16.128.139/eportal/InterFace.do?method=logout")
        self.assertEqual(prepared.data["userIndex"], "abc")
        self.assertEqual(prepared.headers["Origin"], "http://172.16.128.139")

    def test_get_page_info_falls_back_to_manual_json_decode(self) -> None:
        json_content = (FIXTURES / "pageinfo.json").read_text(encoding="utf-8")
        client = CampusLoginClient(make_config(), make_logger(), session_factory=FakeSession)
        client.session = FakeSession(
            post_responses=[
                FakeResponse(
                    content=json_content.encode("utf-8"),
                    json_exc=json.JSONDecodeError("bad", json_content, 0),
                )
            ]
        )
        login_url = "http://172.16.128.139/eportal/index.jsp?foo=bar"

        payload = client._get_page_info(login_url, "foo=bar")

        self.assertEqual(payload["publicKeyExponent"], "10001")
        self.assertIn("publicKeyModulus", payload)

    def test_parse_json_response_uses_custom_decoder_for_gbk_content(self) -> None:
        payload_text = '{"result":"fail","message":"当前用户不在线，请重新认证"}'
        response = FakeResponse(
            content=payload_text.encode("gb18030"),
            json_data={"result": "fail", "message": "ç¨æ·ä¿¡æ¯ä¸å®æ´ï¼è¯·ç¨åéè¯"},
        )
        client = CampusLoginClient(make_config(), make_logger())

        payload = client._parse_json_response(response, action="getOnlineUserInfo")

        self.assertEqual(payload["message"], "当前用户不在线，请重新认证")

    def test_login_uses_mac_value_for_password_encryption(self) -> None:
        redirect_html = (FIXTURES / "redirect.html").read_text(encoding="utf-8").encode("utf-8")
        session = FakeSession(
            get_responses=[
                FakeResponse(content=redirect_html),
                FakeResponse(content=b"<html>login page</html>"),
            ]
        )
        client = CampusLoginClient(make_config(), make_logger(), session_factory=lambda: session)
        page_info = json.loads((FIXTURES / "pageinfo.json").read_text(encoding="utf-8"))

        with (
            patch(
                "campus_login_tool.client.encryptPassword",
                return_value="encrypted",
            ) as encrypt_mock,
            patch.object(
                client,
                "_get_page_info",
                return_value=page_info,
            ),
            patch.object(client, "_submit_login", return_value=(True, "ok")),
        ):
            result = client.login()

        self.assertTrue(result)
        encrypt_mock.assert_called_once()
        self.assertEqual(encrypt_mock.call_args.args[0], "test_password")
        self.assertEqual(encrypt_mock.call_args.args[3], "5fdcd87b4a9fecfe22921ef5c80e86b4")

    def test_login_caches_user_index_from_success_response(self) -> None:
        redirect_html = (FIXTURES / "redirect.html").read_text(encoding="utf-8").encode("utf-8")
        session = FakeSession(
            get_responses=[
                FakeResponse(content=redirect_html),
                FakeResponse(content=b"<html>login page</html>"),
            ]
        )
        client = CampusLoginClient(make_config(), make_logger(), session_factory=lambda: session)
        page_info = json.loads((FIXTURES / "pageinfo.json").read_text(encoding="utf-8"))

        with (
            patch("campus_login_tool.client.encryptPassword", return_value="encrypted"),
            patch.object(client, "_get_page_info", return_value=page_info),
            patch.object(
                client.session if client.session is not None else session,
                "post",
                return_value=FakeResponse(
                    content=b'{"result":"success","message":"","userIndex":"derived-index"}',
                    json_data={"result": "success", "message": "", "userIndex": "derived-index"},
                ),
            ),
        ):
            result = client.login()

        self.assertTrue(result)
        self.assertEqual(client._last_user_index, "derived-index")

    def test_logout_returns_success_when_portal_shows_login_page(self) -> None:
        redirect_html = (FIXTURES / "redirect.html").read_text(encoding="utf-8").encode("utf-8")
        session = FakeSession(
            get_responses=[
                FakeResponse(
                    content=redirect_html,
                    url="http://172.16.128.139/",
                )
            ]
        )
        client = CampusLoginClient(make_config(), make_logger(), session_factory=lambda: session)

        result = client.logout()

        self.assertTrue(result)

    def test_logout_posts_logout_after_online_check(self) -> None:
        session = FakeSession(
            get_responses=[
                FakeResponse(
                    content=b"",
                    url="http://172.16.128.139/eportal/success.jsp?userIndex=user-index",
                )
            ]
        )
        client = CampusLoginClient(make_config(), make_logger(), session_factory=lambda: session)

        with (
            patch.object(
                client,
                "get_online_user_info",
                return_value={"result": "success", "userId": "test_user"},
            ) as info_mock,
            patch.object(
                client,
                "_post_interface_json",
                return_value={"result": "success", "message": "下线成功"},
            ) as post_mock,
        ):
            result = client.logout()

        self.assertTrue(result)
        info_mock.assert_called_once_with("user-index")
        post_mock.assert_called_once_with("logout", {"userIndex": "user-index"})

    def test_logout_retries_when_online_user_info_is_incomplete(self) -> None:
        session = FakeSession(
            get_responses=[
                FakeResponse(
                    content=b"",
                    url="http://172.16.128.139/eportal/success.jsp?userIndex=user-index",
                ),
                FakeResponse(
                    content=b"",
                    url="http://172.16.128.139/eportal/success.jsp?userIndex=user-index-2",
                ),
            ]
        )
        sleep_calls = []
        client = CampusLoginClient(
            make_config(),
            make_logger(),
            session_factory=lambda: session,
            sleep_func=sleep_calls.append,
        )

        with (
            patch.object(
                client,
                "get_online_user_info",
                side_effect=[
                    {"result": "fail", "message": "用户信息不完整，请稍后重试"},
                    {"result": "success", "userId": "test_user"},
                ],
            ) as info_mock,
            patch.object(
                client,
                "_post_interface_json",
                return_value={"result": "success", "message": "下线成功"},
            ) as post_mock,
        ):
            result = client.logout()

        self.assertTrue(result)
        self.assertEqual(sleep_calls, [1])
        self.assertEqual(info_mock.call_args_list[0].args[0], "user-index")
        self.assertEqual(info_mock.call_args_list[1].args[0], "user-index-2")
        post_mock.assert_called_once_with("logout", {"userIndex": "user-index-2"})

    def test_logout_retry_treats_not_online_as_success(self) -> None:
        redirect_html = (FIXTURES / "redirect.html").read_text(encoding="utf-8").encode("utf-8")
        session = FakeSession(
            get_responses=[
                FakeResponse(
                    content=b"",
                    url="http://172.16.128.139/eportal/success.jsp?userIndex=user-index",
                ),
                FakeResponse(
                    content=redirect_html,
                    url="http://172.16.128.139/",
                ),
            ]
        )
        sleep_calls = []
        client = CampusLoginClient(
            make_config(),
            make_logger(),
            session_factory=lambda: session,
            sleep_func=sleep_calls.append,
        )

        with patch.object(
            client,
            "get_online_user_info",
            return_value={"result": "fail", "message": "用户信息不完整，请稍后重试"},
        ):
            result = client.logout()

        self.assertTrue(result)
        self.assertEqual(sleep_calls, [1])

    def test_find_login_page_url_supports_direct_final_url(self) -> None:
        client = CampusLoginClient(make_config(), make_logger())
        response = FakeResponse(
            content=b"<html>login page</html>",
            url=(
                "http://172.16.128.139/eportal/index.jsp?"
                "wlanuserip=1&nasip=2&mac=3"
            ),
        )

        login_url = client._find_login_page_url(response)

        self.assertEqual(
            login_url,
            "http://172.16.128.139/eportal/index.jsp?wlanuserip=1&nasip=2&mac=3",
        )

    def test_login_with_retry_uses_backoff(self) -> None:
        sleep_calls = []
        client = CampusLoginClient(make_config(), make_logger(), sleep_func=sleep_calls.append)
        client.login = Mock(side_effect=[False, False, True])
        client.check_internet = Mock(return_value=True)

        result = client.login_with_retry()

        self.assertTrue(result)
        self.assertEqual(sleep_calls, [2, 4, 2])
