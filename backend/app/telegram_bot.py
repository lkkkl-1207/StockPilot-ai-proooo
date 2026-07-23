from __future__ import annotations

from typing import Any

import requests

from app.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


def send_message(
    text: str,
    chat_id: str | int | None = None,
) -> dict[str, Any]:
    """发送 Telegram 消息。

    chat_id 没有传入时，使用 .env 中保存的默认 Chat ID。
    """

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("没有配置 TELEGRAM_BOT_TOKEN。")

    target_chat_id = (
        str(chat_id)
        if chat_id is not None
        else str(TELEGRAM_CHAT_ID)
    )

    if not target_chat_id:
        raise ValueError("没有配置 TELEGRAM_CHAT_ID。")

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    response = requests.post(
        url,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()
    return response.json()