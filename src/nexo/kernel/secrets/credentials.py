"""Credenciais somente no keyring do SO."""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE = "nexo-cli"


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


LEGACY_SERVICE = "nexo-cli"  # nome anterior do produto


def get_secret(name: str) -> str | None:
    value = keyring.get_password(SERVICE, name)
    return value if isinstance(value, str) else None


def delete_secret(name: str) -> bool:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        return False
    return True
