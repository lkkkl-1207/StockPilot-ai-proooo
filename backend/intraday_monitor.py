from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.config import FINNHUB_API_KEY, TELEGRAM_CHAT_ID
from app.decision_engine_v2 import build_decision
from app.telegram_bot import send_message


BACKEND_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = BACKEND_DIR / "watchlist.json"
STATE_FILE = BACKEND_DIR / "intraday_state.json"
EVENT_LOG_FILE = BACKEND_DIR / "intraday_events.json"

NY_TZ = ZoneInfo("America/New_York")

MIN_SCORE = 60
STRONG_SCORE = 72
MIN_RISK_REWARD = 1.20
STRONG_RISK_REWARD = 1.60


def ny_now() -> datetime:
    return datetime.now(NY_TZ)


def is_regular_market_hours() -> bool:
    now = ny_now()

    return (
        now.weekday() < 5
        and time(9, 30) <= now.time() <= time(16, 0)
    )


def load_watchlist() -> list[str]:
    with WATCHLIST_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    watchlist = payload.get("watchlist", [])

    if not isinstance(watchlist, list):
        raise ValueError("watchlist 格式不正确。")

    return [
        str(symbol).strip().upper()
        for symbol in watchlist
        if str(symbol).strip()
    ]


def empty_state() -> dict[str, Any]:
    return {
        "trading_date": ny_now().date().isoformat(),
        "symbols": {},
    }


def load_state() -> dict[str, Any]:
    today = ny_now().date().isoformat()

    if not STATE_FILE.exists():
        return empty_state()

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            state = json.load(file)

        if state.get("trading_date") != today:
            return empty_state()

        if not isinstance(state.get("symbols"), dict):
            return empty_state()

        return state

    except (OSError, json.JSONDecodeError):
        return empty_state()


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


def load_events() -> dict[str, Any]:
    today = ny_now().date().isoformat()

    if not EVENT_LOG_FILE.exists():
        return {
            "trading_date": today,
            "events": [],
        }

    try:
        with EVENT_LOG_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)

        if payload.get("trading_date") != today:
            return {
                "trading_date": today,
                "events": [],
            }

        return payload

    except (OSError, json.JSONDecodeError):
        return {
            "trading_date": today,
            "events": [],
        }


def save_events(payload: dict[str, Any]) -> None:
    with EVENT_LOG_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )


def record_event(
    symbol: str,
    status: str,
    current_price: float,
    score: int,
    risk_reward: float,
) -> None:
    payload = load_events()

    payload["events"].append(
        {
            "time": ny_now().strftime("%H:%M:%S"),
            "symbol": symbol,
            "status": status,
            "price": round(current_price, 2),
            "score": score,
            "risk_reward": round(risk_reward, 2),
        }
    )

    save_events(payload)


def get_current_quote(symbol: str) -> float:
    if not FINNHUB_API_KEY:
        raise ValueError("没有配置 FINNHUB_API_KEY。")

    response = requests.get(
        "https://finnhub.io/api/v1/quote",
        params={
            "symbol": symbol,
            "token": FINNHUB_API_KEY,
        },
        timeout=20,
    )

    if response.status_code == 429:
        raise RuntimeError("Finnhub 请求次数过多。")

    response.raise_for_status()
    payload = response.json()

    price = float(payload.get("c") or 0)

    if price <= 0:
        raise ValueError(f"{symbol} 没有有效报价。")

    return price


def get_plan_values(
    result: dict[str, Any],
) -> dict[str, float]:
    plan = result["suggested_plan"]
    buy_zone = plan["buy_watch_zone"]

    return {
        "buy_low": float(buy_zone["low"]),
        "buy_high": float(buy_zone["high"]),
        "reference_entry": float(
            plan["reference_entry"]
        ),
        "risk_line": float(
            plan["risk_reference"]
        ),
        "target_1": float(
            plan["first_take_profit"]
        ),
        "target_2": float(
            plan["second_take_profit"]
        ),
        "risk_reward": float(
            plan.get(
                "risk_reward_ratio_to_first_target",
                0,
            )
            or 0
        ),
    }


def determine_status(
    current_price: float,
    score: int,
    values: dict[str, float],
) -> str:
    risk_reward = values["risk_reward"]

    if current_price <= values["risk_line"]:
        return "STOP"

    if current_price >= values["target_2"]:
        return "TP2"

    if current_price >= values["target_1"]:
        return "TP1"

    # 评分和风险收益比不过关，禁止买入信号。
    if (
        score < MIN_SCORE
        or risk_reward < MIN_RISK_REWARD
    ):
        return "NO_TRADE"

    if (
        values["buy_low"]
        <= current_price
        <= values["buy_high"]
    ):
        if (
            score >= STRONG_SCORE
            and risk_reward >= STRONG_RISK_REWARD
        ):
            return "CONFIRMED_ENTRY"

        return "PROBE_ENTRY"

    distance = (
        (current_price - values["buy_high"])
        / values["buy_high"]
        if values["buy_high"] > 0
        else 999
    )

    if 0 < distance <= 0.01:
        return "NEAR_ENTRY"

    if current_price > values["buy_high"]:
        return "WAIT_PULLBACK"

    return "TREND_RECHECK"


def should_notify(
    old_status: str | None,
    new_status: str,
) -> bool:
    actionable = {
        "CONFIRMED_ENTRY",
        "PROBE_ENTRY",
        "NEAR_ENTRY",
        "TP1",
        "TP2",
        "STOP",
    }

    return (
        new_status in actionable
        and new_status != old_status
    )


def signal_details(
    status: str,
) -> tuple[str, str, str]:
    if status == "CONFIRMED_ENTRY":
        return (
            "🔥 短线条件确认",
            "可考虑建立第一笔计划仓位",
            "建议先使用计划仓位的 25%–30%，"
            "不要一次性满仓。",
        )

    if status == "PROBE_ENTRY":
        return (
            "🟢 进入买入观察区",
            "只适合试探性观察",
            "条件仅达到基础标准，"
            "建议不超过计划仓位的 20%。",
        )

    if status == "NEAR_ENTRY":
        return (
            "🟡 接近买入观察区",
            "继续等待",
            "距离观察区不足 1%，"
            "不要提前追价。",
        )

    if status == "TP1":
        return (
            "💰 达到第一止盈",
            "考虑部分锁定利润",
            "可考虑处理计划仓位的 30%–50%，"
            "并保护剩余仓位。",
        )

    if status == "TP2":
        return (
            "🎯 达到第二止盈",
            "短线主要目标已实现",
            "可考虑进一步减仓或退出，"
            "避免利润明显回吐。",
        )

    return (
        "🔴 跌破风险参考线",
        "短线逻辑可能失效",
        "应优先控制风险，不建议盲目补仓。",
    )


def format_alert(
    symbol: str,
    current_price: float,
    status: str,
    result: dict[str, Any],
    values: dict[str, float],
) -> str:
    title, decision, execution = signal_details(
        status
    )

    return (
        f"{title}\n\n"
        f"<b>{symbol}</b>\n"
        f"当前价：${current_price:.2f}\n"
        f"技术评分："
        f"{result['technical_score']}/100\n"
        f"风险收益比："
        f"{values['risk_reward']:.2f}\n\n"
        f"<b>当天短线计划</b>\n"
        f"买入观察区："
        f"${values['buy_low']:.2f} - "
        f"${values['buy_high']:.2f}\n"
        f"参考建仓价："
        f"${values['reference_entry']:.2f}\n"
        f"风险参考线："
        f"${values['risk_line']:.2f}\n"
        f"第一止盈："
        f"${values['target_1']:.2f}\n"
        f"第二止盈："
        f"${values['target_2']:.2f}\n\n"
        f"<b>建议动作</b>\n"
        f"{decision}\n"
        f"{execution}\n\n"
        f"价格与信号可能快速变化，"
        f"本提醒不构成投资建议或收益保证。"
    )


def run_monitor() -> None:
    if not is_regular_market_hours():
        print("当前不在美股常规交易时间。")
        return

    watchlist = load_watchlist()
    state = load_state()
    symbol_states = state["symbols"]

    success_count = 0
    failure_count = 0
    alert_count = 0

    for symbol in watchlist:
        try:
            result = build_decision(symbol)
            current_price = get_current_quote(symbol)
            values = get_plan_values(result)

            score = int(
                result.get("technical_score", 0)
            )

            new_status = determine_status(
                current_price=current_price,
                score=score,
                values=values,
            )

            old_status = symbol_states.get(symbol)

            if should_notify(
                old_status=old_status,
                new_status=new_status,
            ):
                send_message(
                    format_alert(
                        symbol=symbol,
                        current_price=current_price,
                        status=new_status,
                        result=result,
                        values=values,
                    ),
                    chat_id=TELEGRAM_CHAT_ID,
                )

                record_event(
                    symbol=symbol,
                    status=new_status,
                    current_price=current_price,
                    score=score,
                    risk_reward=values[
                        "risk_reward"
                    ],
                )

                alert_count += 1

            symbol_states[symbol] = new_status
            success_count += 1

        except Exception as error:
            failure_count += 1
            print(
                f"{symbol} 检查失败：{error}"
            )

    state["symbols"] = symbol_states
    save_state(state)

    print(
        "盘中监控完成："
        f"成功 {success_count}，"
        f"失败 {failure_count}，"
        f"提醒 {alert_count}。"
    )


if __name__ == "__main__":
    run_monitor()