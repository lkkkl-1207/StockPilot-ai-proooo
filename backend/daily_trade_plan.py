from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import TELEGRAM_CHAT_ID
from app.decision_engine_v2 import build_decision
from app.telegram_bot import send_message


BACKEND_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = BACKEND_DIR / "watchlist.json"

NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")

MINIMUM_SCORE = 60
MINIMUM_RISK_REWARD = 1.20
MAXIMUM_CANDIDATES = 3


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


def get_risk_reward(
    result: dict[str, Any],
) -> float:
    plan = result["suggested_plan"]

    return float(
        plan.get(
            "risk_reward_ratio_to_first_target",
            0,
        )
        or 0
    )


def is_candidate(
    result: dict[str, Any],
) -> bool:
    score = int(result.get("technical_score", 0))
    risk_reward = get_risk_reward(result)

    return (
        score >= MINIMUM_SCORE
        and risk_reward >= MINIMUM_RISK_REWARD
    )


def candidate_priority(
    result: dict[str, Any],
) -> tuple[int, float]:
    return (
        int(result.get("technical_score", 0)),
        get_risk_reward(result),
    )


def format_candidate(
    result: dict[str, Any],
    ranking: int,
) -> str:
    plan = result["suggested_plan"]
    buy_zone = plan["buy_watch_zone"]

    current_price = float(result["current_price"])
    buy_high = float(buy_zone["high"])

    if current_price > buy_high:
        action = "等待回踩，不要追高"
    elif (
        float(buy_zone["low"])
        <= current_price
        <= buy_high
    ):
        action = "已进入观察区，可等待盘中确认"
    else:
        action = "低于原观察区，先确认是否止跌"

    return (
        f"\n<b>{ranking}. {result['symbol']}</b>\n"
        f"评分：{result['technical_score']}/100\n"
        f"当前价：${current_price:.2f}\n"
        f"买入观察区："
        f"${buy_zone['low']:.2f} - "
        f"${buy_zone['high']:.2f}\n"
        f"风险参考线："
        f"${plan['risk_reference']:.2f}\n"
        f"第一止盈："
        f"${plan['first_take_profit']:.2f}\n"
        f"第二止盈："
        f"${plan['second_take_profit']:.2f}\n"
        f"风险收益比："
        f"{get_risk_reward(result):.2f}\n"
        f"今日策略：<b>{action}</b>\n"
    )


def run_daily_trade_plan() -> None:
    watchlist = load_watchlist()

    successful_results: list[dict[str, Any]] = []
    failed_symbols: list[str] = []

    for symbol in watchlist:
        try:
            result = build_decision(symbol)
            successful_results.append(result)

        except Exception as error:
            failed_symbols.append(symbol)
            print(f"{symbol} 分析失败：{error}")

    candidates = [
        result
        for result in successful_results
        if is_candidate(result)
    ]

    candidates.sort(
        key=candidate_priority,
        reverse=True,
    )

    selected = candidates[:MAXIMUM_CANDIDATES]

    rejected = [
        result
        for result in successful_results
        if not is_candidate(result)
    ]

    rejected.sort(
        key=lambda item: int(
            item.get("technical_score", 0)
        )
    )

    now = datetime.now(NEW_YORK_TIMEZONE)

    message = (
        "📊 <b>StockPilot 今日短炒计划</b>\n"
        f"纽约日期：{now:%Y-%m-%d}\n"
        f"观察列表：{len(watchlist)} 只股票\n"
    )

    if selected:
        message += (
            "\n🔥 <b>今日优先关注</b>\n"
        )

        for ranking, result in enumerate(
            selected,
            start=1,
        ):
            message += format_candidate(
                result,
                ranking,
            )
    else:
        message += (
            "\n今天没有股票同时达到评分和"
            "风险收益比标准。\n"
            "<b>今日策略：不强行交易。</b>\n"
        )

    if rejected:
        avoid_symbols = ", ".join(
            result["symbol"]
            for result in rejected[:8]
        )

        message += (
            "\n🚫 <b>暂不建议短炒</b>\n"
            f"{avoid_symbols}\n"
        )

    if failed_symbols:
        message += (
            "\n⚠️ 未完成分析："
            + ", ".join(failed_symbols)
            + "\n"
        )

    message += (
        "\n盘中价格触发买入区、止盈位或"
        "风险线时，机器人会另外提醒。\n\n"
        "以上为技术模型估算，不构成投资建议。"
    )

    send_message(
        message,
        chat_id=TELEGRAM_CHAT_ID,
    )


if __name__ == "__main__":
    run_daily_trade_plan()
    