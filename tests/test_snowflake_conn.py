"""The tools must accept exactly the key formats dbt-snowflake accepts."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tuva_demo.snowflake_conn import load_private_key_der

PASS = "synthetic-test-passphrase"


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def encrypted_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(PASS.encode()),
    ).decode()


def _expected(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def test_full_pem_with_passphrase(key: rsa.RSAPrivateKey, encrypted_pem: str) -> None:
    assert load_private_key_der(encrypted_pem, PASS) == _expected(key)


def test_body_only_without_begin_end_lines(key: rsa.RSAPrivateKey, encrypted_pem: str) -> None:
    body = "\n".join(line for line in encrypted_pem.splitlines() if not line.startswith("-----"))
    assert load_private_key_der(body, PASS) == _expected(key)


def test_surrounding_whitespace_is_ignored(key: rsa.RSAPrivateKey, encrypted_pem: str) -> None:
    assert load_private_key_der(f"\n  {encrypted_pem}\n\n", PASS) == _expected(key)


def test_wrong_passphrase_gives_safe_error(encrypted_pem: str) -> None:
    with pytest.raises(ValueError, match="matching passphrase") as exc:
        load_private_key_der(encrypted_pem, "wrong")
    assert "BEGIN" not in str(exc.value)


def test_empty_key_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        load_private_key_der("   ", PASS)
