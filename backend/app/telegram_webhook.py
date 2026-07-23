from __future__ import annotations

import re
from html import escape
from typing import Any

from app.decision_engine_v2 import build_decision
from app.telegram_bot import send_message


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=-]{1,20}$")


def extract_symbol(text: str) -> str | None:
    """从 Telegram 消息中提取股票代码。"""
    cleaned = text.strip().upper()

    if cleaned.startswith("/START"):
        return None

    if cleaned.startswith("/HELP"):
        return None

    # 支持“分析 NVDA”“查询 TSLA”“NVDA 现在能买吗”等简单输入
    cleaned = re.sub(
        r"^(分析|查询|看看|帮我分析)\s*",
        "",
        cleaned,
    )

    candidates = re.findall(
        r"[A-Z0-9.^=-]{1,20}",
        cleaned,
    )

    for candidate in candidates:
        if SYMBOL_PATTERN.fullmatch(candidate):
            return candidate

    return None


def format_decision(result: dict[str, Any]) -> str:
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

    positive_text = "\n".join(
        f"✅ {escape(str(item))}"
        for item in positive_signals[:3]
    )

    risk_text = "\n".join(
        f"⚠️ {escape(str(item))}"
        for item in risk_signals[:3]
    )

    message = (
        f"📈 <b>{escape(result['symbol'])}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"结论：<b>{escape(result['rating'])}</b>\n"
        f"评分：{result['technical_score']}/100\n"
        f"当前价：${result['current_price']:.2f}\n"
        f"单日变化：{result['daily_change_percent']:.2f}%\n\n"
        f"<b>价格策略</b>\n"
        f"买入观察区："
        f"${buy_zone['low']:.2f} - "
        f"${buy_zone['high']:.2f}\n"
        f"参考建仓价："
        f"${plan['reference_entry']:.2f}\n"
        f"风险参考线："
        f"${plan['risk_reference']:.2f}\n"
        f"第一止盈："
        f"${plan['first_take_profit']:.2f}\n"
        f"第二止盈："
        f"${plan['second_take_profit']:.2f}\n"
    )

    third_target = plan.get("third_take_profit")
    if third_target is not None:
        message += (
            f"第三止盈：${third_target:.2f}\n"
        )

    message += (
        f"风险收益比："
        f"{plan['risk_reward_ratio_to_first_target']:.2f}\n\n"
        f"<b>当前判断</b>\n"
        f"{escape(result['price_status'])}\n"
        f"{escape(result['action'])}\n"
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

    message += (
        "\n以上内容由历史行情和技术指标自动估算，"
        "不构成投资建议。"
    )

    return message


def help_message() -> str:
    return (
        "🤖 <b>StockPilot 使用方法</b>\n\n"
        "直接发送股票代码，例如：\n"
        "<code>NVDA</code>\n"
        "<code>AAPL</code>\n"
        "<code>TSLA</code>\n"
        "<code>700.HK</code>\n\n"
        "也可以发送：\n"
        "<code>分析 AMD</code>\n\n"
        "机器人会返回买入观察区、风险线、"
        "止盈位和分析理由。"
    )


def process_telegram_update(
    update: dict[str, Any],
) -> dict[str, Any]:
    """处理 Telegram 发来的 webhook 更新。"""
    message = update.get("message")

    if not isinstance(message, dict):
        return {
            "ok": True,
            "ignored": True,
        }

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text")

    if chat_id is None or not isinstance(text, str):
        return {
            "ok": True,
            "ignored": True,
        }

    cleaned = text.strip()

    if cleaned.lower() in {
        "/start",
        "/help",
        "help",
        "帮助",
    }:
        send_message(
            help_message(),
            chat_id=chat_id,
        )
        return {
            "ok": True,
            "action": "help",
        }

    symbol = extract_symbol(cleaned)

    if not symbol:
        send_message(
            "没有识别到有效股票代码。\n\n"
            "请直接发送例如："
            "<code>NVDA</code>、"
            "<code>AAPL</code> 或 "
            "<code>700.HK</code>。",
            chat_id=chat_id,
        )
        return {
            "ok": True,
            "action": "invalid_input",
        }

    send_message(
        f"⏳ 正在分析 <b>{escape(symbol)}</b>，"
        "请稍候……",
        chat_id=chat_id,
    )

    try:
        result = build_decision(symbol)
        reply = format_decision(result)

    except Exception as error:
        send_message(
            f"❌ <b>{escape(symbol)}</b> 分析失败\n"
            f"{escape(str(error))}",
            chat_id=chat_id,
        )
        return {
            "ok": False,
            "symbol": symbol,
            "error": str(error),
        }

    send_message(
        reply,
        chat_id=chat_id,
    )

    return {
        "ok": True,
        "symbol": symbol,
        "action": "analysis",
    }