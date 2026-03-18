"""Regression tests for RSA password encryption."""

from __future__ import annotations

import unittest

from campus_login_tool.security import encryptPassword


MODULUS = (
    "94dd2a8675fb779e6b9f7103698634cd400f27a154afa67af6166a43fc264172"
    "22a79506d34cacc7641946abda1785b7acf9910ad6a0978c91ec84d40b71d289"
    "1379af19ffb333e7517e390bd26ac312fe940c340466b4a5d4af1d65c3b59440"
    "78f96a1a51a5a53e4bc302818b7c9f63c4a1b07bd7d874cef1c3d4b2f5eb7871"
)


class SecurityTests(unittest.TestCase):
    def test_encrypt_password_matches_single_block_reference(self) -> None:
        encrypted = encryptPassword(
            "test_password",
            "10001",
            MODULUS,
            "5fdcd87b4a9fecfe22921ef5c80e86b4",
        )

        self.assertEqual(
            encrypted,
            "8df8fdd4d6f5a12fdd2119fb1e8a395c85b2fa69b4989ab688610e83d6a8fa86"
            "e748e011bebf85f77cac7806e433d29bba8390339be9c09a4089a03483ca2d5f"
            "ff6eeea31258d1047a048ef1589caf2c10c92d36eb76658d675eff630e3ee516"
            "dcb522b1ccc1168128a870d44f9d50445af52ad8a77b7dd45f361e159f4c6849",
        )

    def test_encrypt_password_matches_multi_block_reference(self) -> None:
        encrypted = encryptPassword(
            "p" * 140,
            "10001",
            MODULUS,
            "abcdef1234567890abcdef1234567890",
        )

        self.assertEqual(
            encrypted,
            "31ae2117e5a76245366fe7c9a650f5220ef815d0f89041710869e82e60e82b30"
            "902a4ab5cd3ccb89da54f5f899fa0c70b8fb5a627fc889a59cb260dbed66886d"
            "c3f03cce462b13658b5ca6f792dbc6f9cb7163aa530de67db8a89f36481ecf69"
            "d50ec84afbb3c165f302aa09e27c1f7cb542b84d4ddbcfe635fbd9b084764522 "
            "4323adffbeecbb646fa6e5898a140fce0384356078521f6bc2080ab4f95a96b8"
            "d6c45a32aeeb28e546ccf80bf2971b5cf7552e4a35fcc7e4a25a44090cc0a0ca"
            "27d95e4453f2ddb3fc67fecac545255a96fd7e32d704fe930277f89de22b2dcb"
            "3db28988a9fb684324e6a9f9a0c2efbb01a19c532d72f73687e902f9c4d7b94a",
        )
