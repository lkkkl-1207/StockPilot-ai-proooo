from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from app.config import (
    ALPHA_VANTAGE_API_KEY,
    FINNHUB_API_KEY,
)

def _flatten_yfinance_columns(data: pd.DataFrame) -> pd.DataFrame:
    """处理 yfinance 可能返回的多层列名。"""
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    return data


def _download_from_finnhub(symbol: str) -> pd.DataFrame:
    """优先尝试从 Finnhub 获取约一年的日线数据。"""
    if not FINNHUB_API_KEY:
        return pd.DataFrame()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=430)

    response = requests.get(
        "https://finnhub.io/api/v1/stock/candle",
        params={
            "symbol": symbol,
            "resolution": "D",
            "from": int(start_time.timestamp()),
            "to": int(end_time.timestamp()),
            "token": FINNHUB_API_KEY,
        },
        timeout=20,
    )

    if response.status_code != 200:
        return pd.DataFrame()

    payload = response.json()

    if payload.get("s") != "ok":
        return pd.DataFrame()

    required_keys = {"t", "o", "h", "l", "c", "v"}

    if not required_keys.issubset(payload):
        return pd.DataFrame()

    data = pd.DataFrame(
        {
            "Date": pd.to_datetime(payload["t"], unit="s"),
            "Open": payload["o"],
            "High": payload["h"],
            "Low": payload["l"],
            "Close": payload["c"],
            "Volume": payload["v"],
        }
    )

    data = data.set_index("Date").sort_index()

    return data

def _download_from_alpha_vantage(
    symbol: str,
) -> pd.DataFrame:
    """从 Alpha Vantage 获取最近100个交易日的日线数据。"""
    if not ALPHA_VANTAGE_API_KEY:
        return pd.DataFrame()

    response = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "compact",
            "apikey": ALPHA_VANTAGE_API_KEY,
        },
        timeout=30,
    )

    response.raise_for_status()
    payload = response.json()

    time_series = payload.get("Time Series (Daily)")

    if not isinstance(time_series, dict):
        return pd.DataFrame()

    rows = []

    for date_text, values in time_series.items():
        rows.append(
            {
                "Date": pd.to_datetime(date_text),
                "Open": float(values["1. open"]),
                "High": float(values["2. high"]),
                "Low": float(values["3. low"]),
                "Close": float(values["4. close"]),
                "Volume": float(values["5. volume"]),
            }
        )

    if not rows:
        return pd.DataFrame()

    data = pd.DataFrame(rows)
    data = data.set_index("Date").sort_index()

    return data

def _download_from_yfinance(symbol: str) -> pd.DataFrame:
    """Finnhub 日线不可用时，用 yfinance 作为备用数据源。"""
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            data = yf.download(
                symbol,
                period="18mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )

            data = _flatten_yfinance_columns(data)

            if not data.empty:
                return data

        except Exception as error:
            last_error = error

        time.sleep(2 + attempt * 2)

    if last_error:
        raise RuntimeError(
            f"备用历史数据源暂时不可用：{last_error}"
        ) from last_error

    return pd.DataFrame()


def download_price_history(
    symbol: str,
) -> tuple[pd.DataFrame, str]:
    """按稳定性顺序获取历史日线数据。"""
    data = pd.DataFrame()
    source = ""

    try:
        data = _download_from_alpha_vantage(symbol)
    except requests.RequestException:
        data = pd.DataFrame()

    if not data.empty:
        source = "Alpha Vantage"
    else:
        try:
            data = _download_from_finnhub(symbol)
        except requests.RequestException:
            data = pd.DataFrame()

        if not data.empty:
            source = "Finnhub"
        else:
            data = _download_from_yfinance(symbol)
            source = "Yahoo Finance Backup"

    required_columns = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    }

    if data.empty:
        raise ValueError(
            "三个历史数据源都暂时不可用，请稍后重试。"
        )

    if not required_columns.issubset(data.columns):
        raise ValueError("历史行情缺少必要字段。")

    data = data.dropna(
        subset=["High", "Low", "Close"]
    ).copy()

    if len(data) < 80:
        raise ValueError(
            "历史数据不足80个交易日，暂时无法计算可靠区间。"
        )

    return data, source
    """下载历史数据，并返回数据及其来源。"""
    data = pd.DataFrame()

    try:
        data = _download_from_finnhub(symbol)
    except requests.RequestException:
        data = pd.DataFrame()

    if not data.empty:
        source = "Finnhub"
    else:
        data = _download_from_yfinance(symbol)
        source = "Yahoo Finance Backup"

    required_columns = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    }

    if data.empty:
        raise ValueError("没有取得历史行情数据。")

    if not required_columns.issubset(data.columns):
        raise ValueError("历史行情缺少必要字段。")

    data = data.dropna(subset=["High", "Low", "Close"]).copy()

    if len(data) < 80:
        raise ValueError(
            "历史数据不足80个交易日，暂时无法计算可靠区间。"
        )

    return data, source


def calculate_rsi(
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    change = close.diff()

    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    relative_strength = (
        average_gain
        / average_loss.replace(0, np.nan)
    )

    return 100 - (100 / (1 + relative_strength))


def calculate_atr(
    data: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    previous_close = data["Close"].shift(1)

    true_range = pd.concat(
        [
            data["High"] - data["Low"],
            (data["High"] - previous_close).abs(),
            (data["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)

        if np.isnan(result) or np.isinf(result):
            return None

        return result
    except (TypeError, ValueError):
        return None


def _round_or_none(
    value: float | None,
) -> float | None:
    if value is None:
        return None

    return round(value, 2)


def build_decision(symbol: str) -> dict[str, Any]:
    symbol = symbol.strip().upper()

    if not symbol:
        raise ValueError("请输入股票代码。")

    data, data_source = download_price_history(symbol)

    data["MA20"] = data["Close"].rolling(20).mean()
    data["MA50"] = data["Close"].rolling(50).mean()
    data["MA200"] = data["Close"].rolling(200).mean()

    data["RSI14"] = calculate_rsi(data["Close"])
    data["ATR14"] = calculate_atr(data)

    data["EMA12"] = data["Close"].ewm(
        span=12,
        adjust=False,
    ).mean()

    data["EMA26"] = data["Close"].ewm(
        span=26,
        adjust=False,
    ).mean()

    data["MACD"] = data["EMA12"] - data["EMA26"]

    data["MACDSignal"] = data["MACD"].ewm(
        span=9,
        adjust=False,
    ).mean()

    data["BBMiddle"] = data["Close"].rolling(20).mean()

    rolling_std = data["Close"].rolling(20).std()

    data["BBUpper"] = (
        data["BBMiddle"] + 2 * rolling_std
    )

    data["BBLower"] = (
        data["BBMiddle"] - 2 * rolling_std
    )

    latest = data.iloc[-1]
    previous = data.iloc[-2]

    current_price = _safe_float(latest["Close"])
    previous_close = _safe_float(previous["Close"])

    ma20 = _safe_float(latest["MA20"])
    ma50 = _safe_float(latest["MA50"])
    ma200 = _safe_float(latest["MA200"])

    rsi14 = _safe_float(latest["RSI14"])
    atr14 = _safe_float(latest["ATR14"])

    macd = _safe_float(latest["MACD"])
    macd_signal = _safe_float(latest["MACDSignal"])

    bollinger_upper = _safe_float(latest["BBUpper"])
    bollinger_lower = _safe_float(latest["BBLower"])

    if current_price is None or atr14 is None:
        raise ValueError("无法计算最新价格或ATR。")

    recent_20 = data.tail(20)
    recent_60 = data.tail(60)

    support_20 = float(recent_20["Low"].min())
    resistance_20 = float(recent_20["High"].max())

    support_60 = float(recent_60["Low"].min())
    resistance_60 = float(recent_60["High"].max())

    score = 50
    reasons: list[str] = []
    risks: list[str] = []

    if ma20 is not None:
        if current_price >= ma20:
            score += 8
            reasons.append("当前价格位于20日均线上方")
        else:
            score -= 8
            risks.append("当前价格位于20日均线下方")

    if ma20 is not None and ma50 is not None:
        if ma20 >= ma50:
            score += 12
            reasons.append("20日均线高于50日均线，中期趋势偏强")
        else:
            score -= 12
            risks.append("20日均线低于50日均线，中期趋势偏弱")

    if ma50 is not None and ma200 is not None:
        if ma50 >= ma200:
            score += 10
            reasons.append("50日均线高于200日均线，长期结构偏多")
        else:
            score -= 10
            risks.append("50日均线低于200日均线，长期结构偏弱")

    if rsi14 is not None:
        if 40 <= rsi14 <= 60:
            score += 10
            reasons.append("RSI处于相对健康的中性区间")
        elif 60 < rsi14 <= 70:
            score += 4
            reasons.append("RSI偏强，但尚未进入明显超买区")
        elif rsi14 > 70:
            score -= 12
            risks.append("RSI超过70，短线追高风险较大")
        elif rsi14 < 30:
            score += 3
            risks.append("RSI低于30，可能超卖，但下跌趋势仍需确认")

    if macd is not None and macd_signal is not None:
        if macd >= macd_signal:
            score += 8
            reasons.append("MACD位于信号线上方")
        else:
            score -= 8
            risks.append("MACD位于信号线下方")

    if (
        bollinger_upper is not None
        and current_price >= bollinger_upper
    ):
        score -= 8
        risks.append("价格接近或突破布林带上轨，短线偏热")

    score = max(0, min(100, score))

    candidate_supports = [
        support_20,
        support_60,
        ma20,
        ma50,
        bollinger_lower,
    ]

    valid_supports = [
        float(level)
        for level in candidate_supports
        if level is not None
        and level > 0
        and level <= current_price * 1.03
    ]

    if valid_supports:
        nearest_support = max(
            level
            for level in valid_supports
            if level <= current_price * 1.03
        )
    else:
        nearest_support = current_price - atr14

    buy_zone_low = max(
        0.01,
        nearest_support - atr14 * 0.45,
    )

    buy_zone_high = min(
        current_price,
        nearest_support + atr14 * 0.30,
    )

    if buy_zone_low > buy_zone_high:
        buy_zone_low = max(
            0.01,
            current_price - atr14,
        )
        buy_zone_high = current_price

    first_target = max(
        resistance_20,
        current_price + atr14 * 1.5,
    )

    second_target = max(
        resistance_60,
        current_price + atr14 * 2.5,
        first_target + atr14,
    )

     # =========================
    # 建仓、止损、风险收益计算
    # =========================

    entry_reference = (
        buy_zone_low + buy_zone_high
    ) / 2

    # ATR 动态止损
    risk_reference = max(
        0.01,
        entry_reference - atr14 * 2,
    )

    potential_reward = first_target - entry_reference
    potential_risk = entry_reference - risk_reference

    risk_reward_ratio = (
        potential_reward / potential_risk
        if potential_risk > 0
        else 0
    )

    daily_change_percent = (
        ((current_price / previous_close) - 1) * 100
        if previous_close
        and previous_close > 0
        else 0
    )

    # =========================
    # 评级
    # =========================

    if risk_reward_ratio >= 2 and score >= 70:
        rating = "重点关注"
        action = "技术结构和风险收益比较理想，可考虑分批建仓。"

    elif risk_reward_ratio >= 1.5 and score >= 60:
        rating = "可以观察"
        action = "风险收益比较好，可等待合适位置逐步建仓。"

    elif risk_reward_ratio >= 1:
        rating = "耐心等待"
        action = "风险收益比一般，建议等待更好的买点。"

    else:
        rating = "暂不建议入场"
        action = "当前风险收益比不足，建议继续观察。"

     else:
    rating = "暂不建议入场"
    action = "当前风险收益比不足，暂时不适合建立仓位"

    if current_price > buy_zone_high:
        price_status = "当前价格高于观察买入区间，不建议追高"
    elif buy_zone_low <= current_price <= buy_zone_high:
        price_status = "当前价格已进入观察买入区间"
    else:
        price_status = "当前价格低于原观察区间，需要重新确认趋势"

    return {
        "symbol": symbol,
        "current_price": round(current_price, 2),
        "daily_change_percent": round(
            daily_change_percent,
            2,
        ),
        "technical_score": score,
        "rating": rating,
        "action": action,
        "price_status": price_status,
        "suggested_plan": {
            "buy_watch_zone": {
                "low": round(buy_zone_low, 2),
                "high": round(buy_zone_high, 2),
            },
            "reference_entry": round(
                entry_reference,
                2,
            ),
            "risk_reference": round(
                risk_reference,
                2,
            ),
            "first_take_profit": round(
                first_target,
                2,
            ),
            "second_take_profit": round(
                second_target,
                2,
            ),
            "risk_reward_ratio_to_first_target": round(
                risk_reward_ratio,
                2,
            ),
        },
        "indicators": {
            "ma20": _round_or_none(ma20),
            "ma50": _round_or_none(ma50),
            "ma200": _round_or_none(ma200),
            "rsi14": _round_or_none(rsi14),
            "atr14": _round_or_none(atr14),
            "macd": _round_or_none(macd),
            "macd_signal": _round_or_none(
                macd_signal
            ),
            "bollinger_upper": _round_or_none(
                bollinger_upper
            ),
            "bollinger_lower": _round_or_none(
                bollinger_lower
            ),
            "support_20d": round(
                support_20,
                2,
            ),
            "resistance_20d": round(
                resistance_20,
                2,
            ),
            "support_60d": round(
                support_60,
                2,
            ),
            "resistance_60d": round(
                resistance_60,
                2,
            ),
        },
        "positive_signals": reasons,
        "risk_signals": risks,
        "data_source": data_source,
        "important_notice": (
            "买入区间、止损参考与止盈价均由历史行情和技术指标"
            "自动估算，会随市场变化而失效；它们不是保证盈利的"
            "最佳价格，也不构成投资建议。"
        ),
    }