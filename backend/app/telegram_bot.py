import requests

from app.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


def send_message(text: str):

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("没有 TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_CHAT_ID:
        raise ValueError("没有 TELEGRAM_CHAT_ID")

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }

    response = requests.post(
        url,
        json=payload,
        timeout=20,
    )

    response.raise_for_status()

    return response.json()