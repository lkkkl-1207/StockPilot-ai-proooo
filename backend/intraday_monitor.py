from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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

NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")

MINIMUM_SCORE = 60
STRONG_SCORE = 70

MINIMUM_RISK_REWARD = 1.20
STRONG_RISK_REWARD = 1.50


def get_new_york_now() -> datetime:
    return datetime.now(NEW_YORK_TIMEZONE)


def is_regular_market_hours() -> bool:
    now = get_new_york_now()

    if now.weekday() >= 5:
        return False

    market_open = time(9, 30)
    market_close = time(16, 0)

    return market_open <= now.time() <= market_close


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


def load_state() -> dict[str, Any]:
    today = get_new_york_now().date().isoformat()

    if not STATE_FILE.exists():
        return {
            "trading_date": today,
            "symbols": {},
        }

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            state = json.load(file)

        if not isinstance(state, dict):
            raise ValueError

        saved_date = state.get("trading_date")
        symbols = state.get("symbols", {})

        # 每个美股交易日重新开始记录，
        # 允许同一只股票第二天再次提醒。
        if saved_date != today:
            return {
                "trading_date": today,
                "symbols": {},
            }

        if not isinstance(symbols, dict):
            symbols = {}

        return {
            "trading_date": today,
            "symbols": {
                str(symbol): str(status)
                for symbol, status in symbols.items()
            },
        }

    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
    ):
        return {
            "trading_date": today,
            "symbols": {},
        }


def save_state(state: dict[str, Any]) -> None:
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
            "Finnhub 请求次数过多，请稍后再试。"
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
    result: dict[str, Any],
) -> str:
    plan = result["suggested_plan"]
    buy_zone = plan["buy_watch_zone"]

    score = int(result.get("technical_score", 0))

    risk_reward = float(
        plan.get(
            "risk_reward_ratio_to_first_target",
            0,
        )
        or 0
    )

    buy_low = float(buy_zone["low"])
    buy_high = float(buy_zone["high"])
    risk_line = float(plan["risk_reference"])
    first_target = float(plan["first_take_profit"])
    second_target = float(plan["second_take_profit"])

    # 风险线优先级最高。
    if current_price <= risk_line:
        return "STOP_LOSS"

    # 评分或盈亏比不合格，不产生买入提醒。
    if (
        score < MINIMUM_SCORE
        or risk_reward < MINIMUM_RISK_REWARD
    ):
        return "NO_TRADE"

    if current_price >= second_target:
        return "TAKE_PROFIT_2"

    if current_price >= first_target:
        return "TAKE_PROFIT_1"

    if buy_low <= current_price <= buy_high:
        if (
            score >= STRONG_SCORE
            and risk_reward >= STRONG_RISK_REWARD
        ):
            return "STRONG_BUY_ZONE"

        return "BUY_ZONE"

    # 距离买入区上沿不超过1%，提醒接近买点。
    distance_to_buy_zone = (
        (current_price - buy_high) / buy_high
        if buy_high > 0
        else 999
    )

    if 0 < distance_to_buy_zone <= 0.01:
        return "NEAR_BUY_ZONE"

    if current_price > buy_high:
        return "WAIT_PULLBACK"

    return "BELOW_BUY_ZONE"


def should_notify(
    old_status: str | None,
    new_status: str,
) -> bool:
    actionable_statuses = {
        "STRONG_BUY_ZONE",
        "BUY_ZONE",
        "NEAR_BUY_ZONE",
        "TAKE_PROFIT_1",
        "TAKE_PROFIT_2",
        "STOP_LOSS",
    }

    if new_status not in actionable_statuses:
        return False

    # 相同状态当天不重复提醒。
    if old_status == new_status:
        return False

    return True


def format_alert(
    symbol: str,
    current_price: float,
    status: str,
    result: dict[str, Any],
) -> str:
    plan = result["suggested_plan"]
    buy_zone = plan["buy_watch_zone"]

    score = int(result.get("technical_score", 0))

    risk_reward = float(
        plan.get(
            "risk_reward_ratio_to_first_target",
            0,
        )
        or 0
    )

    if status == "STRONG_BUY_ZONE":
        title = "🔥 高优先级短线观察机会"
        action = (
            "价格、评分和风险收益比同时达到标准。"
            "可考虑小仓位分批观察，不要一次性重仓。"
        )

    elif status == "BUY_ZONE":
        title = "🟢 已进入买入观察区"
        action = (
            "条件达到基础标准，可以开始关注。"
            "仍需结合盘中走势确认，不要盲目追单。"
        )

    elif status == "NEAR_BUY_ZONE":
        title = "🟡 正在接近买入观察区"
        action = (
            "距离买入区不足1%，暂时不要追高，"
            "等待价格真正进入区间。"
        )

    elif status == "TAKE_PROFIT_1":
        title = "💰 已达到第一止盈位"
        action = (
            "短线第一目标已经达到，"
            "可考虑部分止盈并保护剩余利润。"
        )

    elif status == "TAKE_PROFIT_2":
        title = "🎯 已达到第二止盈位"
        action = (
            "短线主要目标已经实现，"
            "可考虑进一步减仓或退出。"
        )

    else:
        title = "🔴 已跌破风险参考线"
        action = (
            "原短线交易逻辑可能失效，"
            "应优先控制风险，避免继续扩大损失。"
        )

    return (
        f"{title}\n\n"
        f"<b>{symbol}</b>\n"
        f"当前价：${current_price:.2f}\n"
        f"技术评分：{score}/100\n"
        f"风险收益比：{risk_reward:.2f}\n\n"
        f"<b>短线价格计划</b>\n"
        f"买入观察区："
        f"${buy_zone['low']:.2f} - "
        f"${buy_zone['high']:.2f}\n"
        f"风险参考线："
        f"${plan['risk_reference']:.2f}\n"
        f"第一止盈："
        f"${plan['first_take_profit']:.2f}\n"
        f"第二止盈："
        f"${plan['second_take_profit']:.2f}\n\n"
        f"<b>当前操作</b>\n"
        f"{action}\n\n"
        f"以上价格由技术模型估算，"
        f"不构成投资建议或收益保证。"
    )


def run_monitor() -> None:
    # 手动测试时可能不在交易时间。
    # GitHub定时运行则只在正常交易时间执行分析。
    if not is_regular_market_hours():
        print(
            "当前不在美股常规交易时间，"
            "本次监控结束。"
        )
        return

    watchlist = load_watchlist()
    state = load_state()

    symbol_states = state["symbols"]

    successful_count = 0
    failed_count = 0
    alert_count = 0

    for symbol in watchlist:
        try:
            result = build_decision(symbol)
            quote = get_current_quote(symbol)

            current_price = quote["current_price"]

            new_status = determine_status(
                current_price=current_price,
                result=result,
            )

            old_status = symbol_states.get(symbol)

            if should_notify(
                old_status=old_status,
                new_status=new_status,
            ):
                message = format_alert(
                    symbol=symbol,
                    current_price=current_price,
                    status=new_status,
                    result=result,
                )

                send_message(
                    message,
                    chat_id=TELEGRAM_CHAT_ID,
                )

                alert_count += 1

            symbol_states[symbol] = new_status
            successful_count += 1

        except Exception as error:
            failed_count += 1
            print(
                f"{symbol} 盘中检查失败：{error}"
            )

    state["symbols"] = symbol_states
    save_state(state)

    print(
        "盘中监控完成："
        f"成功 {successful_count}，"
        f"失败 {failed_count}，"
        f"提醒 {alert_count}。"
    )


if __name__ == "__main__":
    run_monitor()
    