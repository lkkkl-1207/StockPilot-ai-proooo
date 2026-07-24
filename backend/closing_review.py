from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import TELEGRAM_CHAT_ID
from app.telegram_bot import send_message


BACKEND_DIR = Path(__file__).resolve().parent
EVENT_LOG_FILE = BACKEND_DIR / "intraday_events.json"

NY_TZ = ZoneInfo("America/New_York")


STATUS_NAMES = {
    "CONFIRMED_ENTRY": "短线条件确认",
    "PROBE_ENTRY": "进入观察区",
    "NEAR_ENTRY": "接近观察区",
    "TP1": "达到第一止盈",
    "TP2": "达到第二止盈",
    "STOP": "跌破风险线",
}


def run_closing_review() -> None:
    today = datetime.now(
        NY_TZ
    ).date().isoformat()

    if not EVENT_LOG_FILE.exists():
        message = (
            "🌙 <b>StockPilot 收盘复盘</b>\n"
            f"纽约日期：{today}\n\n"
            "今天没有触发需要操作的短线信号。\n"
            "没有合适机会时不交易，也是交易纪律。"
        )

        send_message(
            message,
            chat_id=TELEGRAM_CHAT_ID,
        )
        return

    with EVENT_LOG_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    events = payload.get("events", [])

    if not events:
        summary = (
            "今天没有触发需要操作的短线信号。"
        )
    else:
        lines = []

        for event in events:
            status_name = STATUS_NAMES.get(
                event.get("status"),
                event.get("status", "未知状态"),
            )

            lines.append(
                f"• {event.get('time')} "
                f"<b>{event.get('symbol')}</b>\n"
                f"  {status_name}｜"
                f"${event.get('price')}\n"
                f"  评分 {event.get('score')}｜"
                f"盈亏比 {event.get('risk_reward')}"
            )

        summary = "\n\n".join(lines)

    message = (
        "🌙 <b>StockPilot 收盘复盘</b>\n"
        f"纽约日期：{today}\n\n"
        f"{summary}\n\n"
        "复盘只记录模型实际发出的信号，"
        "不代表这些信号一定盈利。"
    )

    send_message(
        message,
        chat_id=TELEGRAM_CHAT_ID,
    )


if __name__ == "__main__":
    run_closing_review()