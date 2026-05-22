"""Проверка Telegram WebApp initData."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import parse_qsl


def get_bot_token() -> str:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required for Mini App auth")
    return token


def validate_init_data(init_data: str, max_age_sec: int = 86400) -> dict[str, Any]:
    """
    Проверяет подпись initData и возвращает распарсенные поля.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        raise ValueError("initData is empty")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("hash missing")

    auth_date = parsed.get("auth_date")
    if auth_date:
        try:
            if time.time() - int(auth_date) > max_age_sec:
                raise ValueError("initData expired")
        except ValueError as e:
            if str(e) == "initData expired":
                raise
            raise ValueError("invalid auth_date") from e

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(
        b"WebAppData", get_bot_token().encode(), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("invalid initData signature")

    result: dict[str, Any] = dict(parsed)
    if "user" in result:
        result["user"] = json.loads(result["user"])
    return result


def user_from_init_data(init_data: str) -> dict[str, Any]:
    data = validate_init_data(init_data)
    user = data.get("user")
    if not isinstance(user, dict) or "id" not in user:
        raise ValueError("user missing in initData")
    return user
