"""Shared Snowflake connection for the CLI tools (key-pair auth from env vars).

PHI: none. Connects to the isolated TUVA_DEMO database only.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snowflake.connector import SnowflakeConnection


def _normalize_private_key_pem(pem: str) -> str:
    """Accept both raw PEM text and GitHub-style escaped newline values.

    GitHub Actions secrets can arrive with literal backslash-n sequences when a
    secret is sourced from JSON or a shell-escaped environment. The Snowflake
    connector expects actual PEM newlines, so normalize both forms before parsing.
    """
    text = (pem or "").strip()
    if not text:
        raise ValueError("SNOWFLAKE_PRIVATE_KEY is empty")
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\r")
    return text.strip()


def load_private_key_der(pem: str, passphrase: str | None) -> bytes:
    from cryptography.hazmat.primitives import serialization

    normalized_pem = _normalize_private_key_pem(pem)
    password = passphrase.strip().encode() if passphrase and passphrase.strip() else None
    try:
        key = serialization.load_pem_private_key(
            normalized_pem.encode(), password=password
        )
    except ValueError as exc:
        raise ValueError(
            "Could not parse SNOWFLAKE_PRIVATE_KEY. It must contain a PEM private key "
            "(with actual newlines, or escaped \\n sequences if injected from JSON/shell)."
        ) from exc
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect_from_env(default_role: str, tag_suffix: str) -> SnowflakeConnection:
    import snowflake.connector

    env = os.environ
    private_key = env.get("SNOWFLAKE_PRIVATE_KEY")
    if not private_key:
        raise ValueError("Missing required env var: SNOWFLAKE_PRIVATE_KEY")
    return snowflake.connector.connect(
        account=env["SNOWFLAKE_ACCOUNT"],
        user=env.get("SNOWFLAKE_USER", "TUVA_DEMO_SVC"),
        private_key=load_private_key_der(
            private_key, env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None
        ),
        role=env.get("SNOWFLAKE_ROLE", default_role),
        warehouse=env.get("SNOWFLAKE_WAREHOUSE", "TUVA_DEMO_WH"),
        database=env.get("SNOWFLAKE_DATABASE", "TUVA_DEMO"),
        session_parameters={"QUERY_TAG": f"{env.get('DBT_QUERY_TAG', 'tuva-demo:local')}|{tag_suffix}"},
        login_timeout=30,
    )
