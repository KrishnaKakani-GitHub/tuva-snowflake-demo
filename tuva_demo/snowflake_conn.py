"""Shared Snowflake connection for the CLI tools (key-pair auth from env vars).

PHI: none. Connects to the isolated TUVA_DEMO database only.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snowflake.connector import SnowflakeConnection


def load_private_key_der(pem: str, passphrase: str | None) -> bytes:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(
        pem.encode(), password=passphrase.encode() if passphrase else None
    )
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
            env["SNOWFLAKE_PRIVATE_KEY"], env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None
        ),
        role=env.get("SNOWFLAKE_ROLE", default_role),
        warehouse=env.get("SNOWFLAKE_WAREHOUSE", "TUVA_DEMO_WH"),
        database=env.get("SNOWFLAKE_DATABASE", "TUVA_DEMO"),
        session_parameters={"QUERY_TAG": f"{env.get('DBT_QUERY_TAG', 'tuva-demo:local')}|{tag_suffix}"},
        login_timeout=30,
    )
