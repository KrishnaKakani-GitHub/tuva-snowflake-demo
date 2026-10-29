"""Shared Snowflake connection for the CLI tools (key-pair auth from env vars).

The private key is parsed with dbt-snowflake's own loader, so these tools accept
exactly the formats `dbt debug` / `dbt build` accept from the same secret:
full PEM text, or the base64 key body without BEGIN/END lines (DER).

PHI: none. Connects to the isolated TUVA_DEMO database only.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snowflake.connector import SnowflakeConnection


def load_private_key_der(key_text: str, passphrase: str | None) -> bytes:
    """Return the key as unencrypted PKCS8 DER bytes for snowflake.connector."""
    from cryptography.hazmat.primitives import serialization
    from dbt.adapters.snowflake.auth import private_key_from_string

    text = (key_text or "").strip()
    if not text:
        raise ValueError("SNOWFLAKE_PRIVATE_KEY is empty")
    try:
        key = private_key_from_string(text, passphrase or None)
    except (ValueError, TypeError) as exc:
        # Never include key material in the message.
        raise ValueError(
            "Could not load SNOWFLAKE_PRIVATE_KEY with the given passphrase. Expected the "
            "contents of the .p8 file (PEM, or its base64 body) and the matching passphrase."
        ) from exc
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect_from_env(default_role: str, tag_suffix: str) -> SnowflakeConnection:
    import snowflake.connector

    env = os.environ
    return snowflake.connector.connect(
        account=env["SNOWFLAKE_ACCOUNT"],
        user=env.get("SNOWFLAKE_USER", "TUVA_DEMO_SVC"),
        private_key=load_private_key_der(
            env.get("SNOWFLAKE_PRIVATE_KEY", ""), env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        ),
        role=env.get("SNOWFLAKE_ROLE", default_role),
        warehouse=env.get("SNOWFLAKE_WAREHOUSE", "TUVA_DEMO_WH"),
        database=env.get("SNOWFLAKE_DATABASE", "TUVA_DEMO"),
        session_parameters={"QUERY_TAG": f"{env.get('DBT_QUERY_TAG', 'tuva-demo:local')}|{tag_suffix}"},
        login_timeout=30,
    )
