from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from app.decision_engine_v2 import build_decision
from app.telegram_bot import send_message


BACKEND_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = BACKEND_DIR / "watchlist.json"


def load_settings() -> dict[str, Any]:
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

    if not isinstance(watchlist, list) or not watchlist:
        raise ValueError(
            "watchlist.json 中没有有效的股票代码。"
        )

    settings["watchlist"] = [
        str(symbol).strip().upper()
        for symbol in watchlist
        if str(symbol).strip()
    ]

    settings["minimum_score"] = int(
        settings.get("minimum_score", 70)
    )

    settings["maximum_results"] = int(
        settings.get("maximum_results", 3)
    )

    return settings


def format_candidate(
    result: dict[str, Any],
    ranking: int,
) -> str:
    plan = result["suggested_plan"]
    buy_zone = plan["buy_watch_zone"]

    positive_signals = result.get(
        "positive_signals",
        [],
    )

    risk_signals = result.get(
        "risk_signals",
        [],
    )

    reasons = "\n".join(
        f"• {escape(str(reason))}"
        for reason in positive_signals[:2]
    )

    risks = "\n".join(
        f"• {escape(str(reason))}"
        for reason in risk_signals[:2]
    )

    text = (
        f"\n<b>{ranking}. {escape(result['symbol'])}</b>\n"
        f"结论：<b>{escape(result['rating'])}</b>\n"
        f"评分：{result['technical_score']}/100\n"
        f"当前价：${result['current_price']:.2f}\n"
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
        f"{plan['risk_reward_ratio_to_first_target']:.2f}\n"
        f"判断：{escape(result['price_status'])}\n"
    )

    if reasons:
        text += f"\n<b>支持理由</b>\n{reasons}\n"

    if risks:
        text += f"\n<b>主要风险</b>\n{risks}\n"

    return text


def run_daily_report() -> None:
    settings = load_settings()

    watchlist = settings["watchlist"]
    minimum_score = settings["minimum_score"]
    maximum_results = settings["maximum_results"]

    successful_results: list[dict[str, Any]] = []
    failed_symbols: list[str] = []

    for symbol in watchlist:
        try:
            result = build_decision(symbol)
            successful_results.append(result)

        except Exception:
            failed_symbols.append(symbol)

    successful_results.sort(
        key=lambda item: (
            item["technical_score"],
            item["suggested_plan"][
                "risk_reward_ratio_to_first_target"
            ],
        ),
        reverse=True,
    )

    qualified = [
        result
        for result in successful_results
        if result["technical_score"] >= minimum_score
    ]

    selected = qualified[:maximum_results]

    today = datetime.now().strftime("%Y-%m-%d")

    message = (
        f"📊 <b>StockPilot 每日投资简报</b>\n"
        f"日期：{today}\n"
        f"已分析：{len(successful_results)} 只股票\n"
    )

    if selected:
        message += (
            f"\n<b>今日值得重点关注</b>\n"
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
            "\n今天没有股票达到当前筛选标准。\n"
            "建议继续等待，不必为了交易而交易。\n"
        )

    if failed_symbols:
        message += (
            "\n未完成分析："
            + ", ".join(failed_symbols)
            + "\n"
        )

    message += (
        "\n以上价格为模型根据历史行情估算的"
        "观察和风险参考，不构成投资建议。"
    )

    send_message(message)


if __name__ == "__main__":
    run_daily_report()