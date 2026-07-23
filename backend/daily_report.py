from __future__ import annotations

from datetime import datetime
from html import escape

from app.decision_engine_v2 import build_decision
from app.telegram_bot import send_message


WATCHLIST = [
    "NVDA",
    "AAPL",
    "MSFT",
    "META",
    "AMD",
    "TSLA",
]


def format_stock_report(result: dict) -> str:
    symbol = result["symbol"]
    current_price = result["current_price"]
    score = result["technical_score"]
    rating = result["rating"]
    action = result["action"]
    price_status = result["price_status"]

    plan = result["suggested_plan"]

    buy_zone = plan["buy_watch_zone"]
    buy_low = buy_zone["low"]
    buy_high = buy_zone["high"]

    reference_entry = plan["reference_entry"]
    risk_reference = plan["risk_reference"]
    first_take_profit = plan["first_take_profit"]
    second_take_profit = plan["second_take_profit"]
    third_take_profit = plan.get("third_take_profit")
    risk_reward_ratio = plan[
        "risk_reward_ratio_to_first_target"
    ]

    positive_signals = result.get(
        "positive_signals",
        [],
    )

    risk_signals = result.get(
        "risk_signals",
        [],
    )

    positive_text = "\n".join(
        f"✅ {escape(str(reason))}"
        for reason in positive_signals[:3]
    )

    risk_text = "\n".join(
        f"⚠️ {escape(str(reason))}"
        for reason in risk_signals[:3]
    )

    message = (
        f"<b>{escape(symbol)}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"当前价格：${current_price:.2f}\n"
        f"技术评分：{score}/100\n"
        f"结论：<b>{escape(rating)}</b>\n\n"
        f"<b>价格策略</b>\n"
        f"观察买入区：${buy_low:.2f} - ${buy_high:.2f}\n"
        f"参考建仓价：${reference_entry:.2f}\n"
        f"风险参考线：${risk_reference:.2f}\n"
        f"第一止盈：${first_take_profit:.2f}\n"
        f"第二止盈：${second_take_profit:.2f}\n"
    )

    if third_take_profit is not None:
        message += (
            f"第三止盈：${third_take_profit:.2f}\n"
        )

    message += (
        f"风险收益比：{risk_reward_ratio:.2f}\n\n"
        f"<b>当前判断</b>\n"
        f"{escape(price_status)}\n"
        f"{escape(action)}\n"
    )

    if positive_text:
        message += (
            f"\n<b>支持理由</b>\n"
            f"{positive_text}\n"
        )

    if risk_text:
        message += (
            f"\n<b>主要风险</b>\n"
            f"{risk_text}\n"
        )

    return message


def run_daily_report() -> None:
    today = datetime.now().strftime("%Y-%m-%d")

    send_message(
        f"📊 <b>StockPilot 每日分析</b>\n"
        f"日期：{today}\n"
        f"正在分析 {len(WATCHLIST)} 只股票……"
    )

    successful_reports = 0
    failed_symbols: list[str] = []

    for symbol in WATCHLIST:
        try:
            result = build_decision(symbol)
            report = format_stock_report(result)
            send_message(report)
            successful_reports += 1

        except Exception as error:
            failed_symbols.append(symbol)

            send_message(
                f"❌ <b>{escape(symbol)}</b> 分析失败\n"
                f"{escape(str(error))}"
            )

    summary = (
        f"✅ <b>每日分析完成</b>\n"
        f"成功：{successful_reports} 只\n"
        f"失败：{len(failed_symbols)} 只"
    )

    if failed_symbols:
        summary += (
            "\n失败股票："
            + ", ".join(failed_symbols)
        )

    summary += (
        "\n\n所有价格均为技术模型估算，"
        "不构成投资建议。"
    )

    send_message(summary)


if __name__ == "__main__":
    run_daily_report()