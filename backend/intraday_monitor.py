from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from app.config import (
    FINNHUB_API_KEY,
    TELEGRAM_CHAT_ID,
)
from app.decision_engine_v2 import build_decision
from app.telegram_bot import send_message


BACKEND_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = BACKEND_DIR / "watchlist.json"
STATE_FILE = BACKEND_DIR / "intraday_state.json"


def load_watchlist() -> list[str]:
    if not WATCHLIST_FILE.exists():
        raise FileNotFoundError(
            "找不到 backend/watchlist.json。"
        )

    with WATCHLIST_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        settings = json.load(file)

    watchlist = settings.get("watchlist", [])

    if not isinstance(watchlist, list):
        raise ValueError(
            "watchlist.json 中的 watchlist 格式不正确。"
        )

    return [
        str(symbol).strip().upper()
        for symbol in watchlist
        if str(symbol).strip()
    ]


def load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            state = json.load(file)

        if isinstance(state, dict):
            return {
                str(key): str(value)
                for key, value in state.items()
            }

    except (OSError, json.JSONDecodeError):
        pass

    return {}


def save_state(state: dict[str, str]) -> None:
    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
        )


def get_current_quote(
    symbol: str,
) -> dict[str, float]:
    if not FINNHUB_API_KEY:
        raise ValueError(
            "没有配置 FINNHUB_API_KEY。"
        )

    response = requests.get(
        "https://finnhub.io/api/v1/quote",
        params={
            "symbol": symbol,
            "token": FINNHUB_API_KEY,
        },
        timeout=20,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "Finnhub 请求次数过多。"
        )

    response.raise_for_status()
    payload = response.json()

    current_price = float(payload.get("c") or 0)

    if current_price <= 0:
        raise ValueError(
            f"{symbol} 没有取得有效当前价格。"
        )

    return {
        "current_price": current_price,
        "day_high": float(payload.get("h") or 0),
        "day_low": float(payload.get("l") or 0),
        "day_open": float(payload.get("o") or 0),
        "previous_close": float(payload.get("pc") or 0),
    }


def determine_status(
    current_price: float,
    plan: dict[str, Any],
) -> str:
    buy_zone = plan["buy_watch_zone"]

    buy_low = float(buy_zone["low"])
    buy_high = float(buy_zone["high"])
    risk_line = float(plan["risk_reference"])
    first_target = float(plan["first_take_profit"])
    second_target = float(plan["second_take_profit"])

    if current_price <= risk_line:
        return "STOP_LOSS"

    if current_price >= second_target:
        return "TAKE_PROFIT_2"

    if current_price >= first_target:
        return "TAKE_PROFIT_1"

    if buy_low <= current_price <= buy_high:
        return "BUY_ZONE"

    if current_price > buy_high:
        return "WAIT_PULLBACK"

    return "BELOW_BUY_ZONE"


def format_alert(
    symbol: str,
    current_price: float,
    status: str,
    result: dict[str, Any],
) -> str:
    plan = result["suggested_plan"]
    buy_zone = plan["buy_watch_zone"]

    if status == "BUY_ZONE":
        title = "🟢 已进入买入观察区"
        action = "可以开始关注，但不要一次性重仓。"

    elif status == "TAKE_PROFIT_1":
        title = "💰 已达到第一止盈位"
        action = "可以考虑部分止盈，并上调风险保护。"

    elif status == "TAKE_PROFIT_2":
        title = "🎯 已达到第二止盈位"
        action = "短线目标已经充分实现，可考虑进一步减仓。"

    elif status == "STOP_LOSS":
        title = "🔴 已跌破风险参考线"
        action = "短线交易逻辑可能失效，应优先控制风险。"

    elif status == "WAIT_PULLBACK":
        title = "🟡 当前高于买入区"
        action = "不建议追高，继续等待回踩。"

    else:
        title = "⚠️ 当前低于原买入区"
        action = "不要急于抄底，需要重新确认趋势。"

    return (
        f"{title}\n\n"
        f"<b>{symbol}</b>\n"
        f"当前价：${current_price:.2f}\n"
        f"技术评分：{result['technical_score']}/100\n\n"
        f"买入观察区："
        f"${buy_zone['low']:.2f} - "
        f"${buy_zone['high']:.2f}\n"
        f"风险参考线："
        f"${plan['risk_reference']:.2f}\n"
        f"第一止盈："
        f"${plan['first_take_profit']:.2f}\n"
        f"第二止盈："
        f"${plan['second_take_profit']:.2f}\n\n"
        f"{action}\n\n"
        f"以上为技术模型估算，不构成投资建议。"
    )


def run_monitor() -> None:
    watchlist = load_watchlist()
    previous_state = load_state()
    new_state = dict(previous_state)

    for symbol in watchlist:
        try:
            result = build_decision(symbol)
            quote = get_current_quote(symbol)

            current_price = quote["current_price"]
            plan = result["suggested_plan"]

            current_status = determine_status(
                current_price=current_price,
                plan=plan,
            )

            old_status = previous_state.get(symbol)

            if current_status != old_status:
                # 第一次运行时，仅对真正需要操作的状态提醒。
                should_notify = (
                    old_status is not None
                    or current_status
                    in {
                        "BUY_ZONE",
                        "TAKE_PROFIT_1",
                        "TAKE_PROFIT_2",
                        "STOP_LOSS",
                    }
                )

                if should_notify:
                    message = format_alert(
                        symbol=symbol,
                        current_price=current_price,
                        status=current_status,
                        result=result,
                    )

                    send_message(
                        message,
                        chat_id=TELEGRAM_CHAT_ID,
                    )

                new_state[symbol] = current_status

        except Exception as error:
            print(
                f"{symbol} 盘中检查失败：{error}"
            )

    save_state(new_state)


if __name__ == "__main__":
    run_monitor()
    