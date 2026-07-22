from typing import Any

import numpy as np
import pandas as pd
import requests
from app.config import (
    FINNHUB_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
)
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pathlib import Path
from fastapi.responses import FileResponse
from app.decision_engine import build_decision

app = FastAPI(
    title="StockPilot AI Pro",
    version="0.2.0",
    description="AI Stock Research Platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "project": "StockPilot AI Pro",
        "version": "0.2.0",
        "status": "running",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    price_change = close.diff()

    gains = price_change.clip(lower=0)
    losses = -price_change.clip(upper=0)

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

    relative_strength = average_gain / average_loss.replace(0, np.nan)

    return 100 - (100 / (1 + relative_strength))


def calculate_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
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


def prepare_history(symbol: str) -> pd.DataFrame:
    data = yf.download(
        symbol,
        period="1y",
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    if data.empty:
        raise ValueError("没有取得股票数据，请检查股票代码。")

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required_columns = {"Open", "High", "Low", "Close", "Volume"}

    if not required_columns.issubset(data.columns):
        raise ValueError("取得的数据缺少必要字段。")

    data = data.dropna(subset=["Close"]).copy()

    if len(data) < 60:
        raise ValueError("历史数据不足，暂时无法进行完整分析。")

    return data


def score_stock(
    price: float,
    ma20: float,
    ma50: float,
    rsi: float,
    volume_ratio: float,
) -> tuple[int, list[str]]:
    score = 50
    reasons: list[str] = []

    if price > ma20:
        score += 10
        reasons.append("价格位于20日均线上方，短期趋势偏强")
    else:
        score -= 10
        reasons.append("价格位于20日均线下方，短期趋势偏弱")

    if ma20 > ma50:
        score += 15
        reasons.append("20日均线高于50日均线，中期趋势偏多")
    else:
        score -= 15
        reasons.append("20日均线低于50日均线，中期趋势偏弱")

    if 40 <= rsi <= 60:
        score += 10
        reasons.append("RSI处于中性区间，没有明显追高")
    elif rsi < 30:
        score += 5
        reasons.append("RSI偏低，可能出现超卖，但仍需控制风险")
    elif rsi > 70:
        score -= 15
        reasons.append("RSI偏高，短期追高风险较大")

    if volume_ratio >= 1.3:
        score += 5
        reasons.append("当前成交量高于20日平均成交量")
    elif volume_ratio < 0.7:
        score -= 3
        reasons.append("当前成交量偏低，市场参与度不足")

    return max(0, min(100, score)), reasons


@app.get("/analyze/{symbol}")
def analyze_stock(symbol: str) -> dict[str, Any]:
    symbol = symbol.strip().upper()

    if not symbol:
        raise HTTPException(status_code=400, detail="请输入股票代码。")

    try:
        data = prepare_history(symbol)

        data["MA20"] = data["Close"].rolling(20).mean()
        data["MA50"] = data["Close"].rolling(50).mean()
        data["RSI14"] = calculate_rsi(data["Close"])
        data["ATR14"] = calculate_atr(data)
        data["AverageVolume20"] = data["Volume"].rolling(20).mean()

        latest = data.iloc[-1]
        previous = data.iloc[-2]

        price = float(latest["Close"])
        previous_close = float(previous["Close"])

        ma20 = float(latest["MA20"])
        ma50 = float(latest["MA50"])
        rsi = float(latest["RSI14"])
        atr = float(latest["ATR14"])

        average_volume = float(latest["AverageVolume20"])
        current_volume = float(latest["Volume"])

        volume_ratio = (
            current_volume / average_volume
            if average_volume > 0
            else 1.0
        )

        recent_20_days = data.tail(20)

        support = float(recent_20_days["Low"].min())
        resistance = float(recent_20_days["High"].max())

        score, reasons = score_stock(
            price=price,
            ma20=ma20,
            ma50=ma50,
            rsi=rsi,
            volume_ratio=volume_ratio,
        )

        buy_zone_low = max(0.01, support - atr * 0.25)
        buy_zone_high = min(price, support + atr * 0.50)

        take_profit_1 = max(
            resistance,
            price + atr * 1.50,
        )

        take_profit_2 = max(
            take_profit_1,
            price + atr * 2.50,
        )

        risk_line = max(
            0.01,
            support - atr,
        )

        daily_change = (
            ((price / previous_close) - 1) * 100
            if previous_close > 0
            else 0
        )

        if score >= 75:
            conclusion = "趋势偏强，可列入重点观察"
        elif score >= 55:
            conclusion = "走势中性，等待更有吸引力的价格"
        else:
            conclusion = "走势偏弱，暂时谨慎"

        return {
            "symbol": symbol,
            "current_price": round(price, 2),
            "daily_change_percent": round(daily_change, 2),
            "technical_score": score,
            "conclusion": conclusion,
            "indicators": {
                "ma20": round(ma20, 2),
                "ma50": round(ma50, 2),
                "rsi14": round(rsi, 2),
                "atr14": round(atr, 2),
                "volume_ratio": round(volume_ratio, 2),
                "support_20d": round(support, 2),
                "resistance_20d": round(resistance, 2),
            },
            "price_zones": {
                "buy_watch_zone": {
                    "low": round(buy_zone_low, 2),
                    "high": round(buy_zone_high, 2),
                },
                "take_profit_zone": {
                    "first": round(take_profit_1, 2),
                    "second": round(take_profit_2, 2),
                },
                "risk_reference": round(risk_line, 2),
            },
            "reasons": reasons,
            "important_notice": (
                "以上价格仅为根据历史行情和技术指标计算的观察区间，"
                "不是保证获利的最佳买卖价格，也不构成投资建议。"
            ),
        }

    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"分析失败：{error}",
        ) from error
@app.get("/quote/{symbol}")
def get_realtime_quote(symbol: str) -> dict[str, Any]:
    symbol = symbol.strip().upper()

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="请输入股票代码。",
        )

    if not FINNHUB_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="FINNHUB_API_KEY 尚未设置。",
        )

    try:
        response = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={
                "symbol": symbol,
                "token": FINNHUB_API_KEY,
            },
            timeout=15,
        )

        if response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="Finnhub 请求次数过多，请稍后再试。",
            )

        response.raise_for_status()
        data = response.json()

        current_price = float(data.get("c") or 0)
        previous_close = float(data.get("pc") or 0)

        if current_price <= 0:
            raise HTTPException(
                status_code=404,
                detail="没有取得有效报价，请检查股票代码。",
            )

        change_percent = (
            ((current_price / previous_close) - 1) * 100
            if previous_close > 0
            else 0
        )

        return {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "change": round(float(data.get("d") or 0), 2),
            "change_percent": round(change_percent, 2),
            "day_high": round(float(data.get("h") or 0), 2),
            "day_low": round(float(data.get("l") or 0), 2),
            "day_open": round(float(data.get("o") or 0), 2),
            "previous_close": round(previous_close, 2),
            "timestamp": int(data.get("t") or 0),
            "data_source": "Finnhub",
        }

    except HTTPException:
        raise

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接 Finnhub：{error}",
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"取得报价失败：{error}",
        ) from error

@app.get("/research/{symbol}")
def get_stock_research(symbol: str) -> dict[str, Any]:
    from datetime import datetime, timedelta, timezone

    symbol = symbol.strip().upper()

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="请输入股票代码。",
        )

    if not FINNHUB_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="FINNHUB_API_KEY 尚未设置。",
        )

    try:
        quote_response = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={
                "symbol": symbol,
                "token": FINNHUB_API_KEY,
            },
            timeout=15,
        )
        quote_response.raise_for_status()
        quote_data = quote_response.json()

        current_price = float(quote_data.get("c") or 0)
        previous_close = float(quote_data.get("pc") or 0)

        if current_price <= 0:
            raise HTTPException(
                status_code=404,
                detail="没有取得有效报价，请检查股票代码。",
            )

        profile_response = requests.get(
            "https://finnhub.io/api/v1/stock/profile2",
            params={
                "symbol": symbol,
                "token": FINNHUB_API_KEY,
            },
            timeout=15,
        )
        profile_response.raise_for_status()
        profile_data = profile_response.json()

        today = datetime.now(timezone.utc).date()
        seven_days_ago = today - timedelta(days=7)

        news_response = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": symbol,
                "from": seven_days_ago.isoformat(),
                "to": today.isoformat(),
                "token": FINNHUB_API_KEY,
            },
            timeout=15,
        )
        news_response.raise_for_status()
        news_data = news_response.json()

        if not isinstance(news_data, list):
            news_data = []

        positive_words = [
            "growth",
            "beat",
            "upgrade",
            "record",
            "strong",
            "surge",
            "profit",
            "partnership",
            "launch",
            "approval",
        ]

        negative_words = [
            "decline",
            "miss",
            "downgrade",
            "lawsuit",
            "weak",
            "risk",
            "cut",
            "loss",
            "investigation",
            "restriction",
        ]

        positive_count = 0
        negative_count = 0

        processed_news = []

        for article in news_data[:10]:
            headline = str(article.get("headline") or "")
            summary = str(article.get("summary") or "")
            combined_text = f"{headline} {summary}".lower()

            positive_hits = sum(
                1 for word in positive_words if word in combined_text
            )
            negative_hits = sum(
                1 for word in negative_words if word in combined_text
            )

            positive_count += positive_hits
            negative_count += negative_hits

            if positive_hits > negative_hits:
                article_sentiment = "偏正面"
            elif negative_hits > positive_hits:
                article_sentiment = "偏负面"
            else:
                article_sentiment = "中性"

            processed_news.append(
                {
                    "headline": headline,
                    "summary": summary,
                    "source": article.get("source") or "",
                    "url": article.get("url") or "",
                    "published_timestamp": int(
                        article.get("datetime") or 0
                    ),
                    "sentiment": article_sentiment,
                }
            )

        total_sentiment_hits = positive_count + negative_count

        if total_sentiment_hits == 0:
            news_score = 50
        else:
            news_score = round(
                positive_count
                / total_sentiment_hits
                * 100
            )

        if news_score >= 65:
            news_conclusion = "近期新闻整体偏正面"
        elif news_score <= 35:
            news_conclusion = "近期新闻整体偏负面"
        else:
            news_conclusion = "近期新闻整体偏中性"

        change_percent = (
            ((current_price / previous_close) - 1) * 100
            if previous_close > 0
            else 0
        )

        preliminary_score = 50

        if change_percent > 1:
            preliminary_score += 10
        elif change_percent < -1:
            preliminary_score -= 10

        if news_score >= 65:
            preliminary_score += 15
        elif news_score <= 35:
            preliminary_score -= 15

        preliminary_score = max(
            0,
            min(100, preliminary_score),
        )

        if preliminary_score >= 70:
            preliminary_conclusion = "当前信息偏积极，可继续深入研究"
        elif preliminary_score >= 50:
            preliminary_conclusion = "当前信息中性，等待更多确认"
        else:
            preliminary_conclusion = "当前信息偏弱，需要谨慎"

        return {
            "symbol": symbol,
            "company": {
                "name": profile_data.get("name") or symbol,
                "country": profile_data.get("country") or "",
                "exchange": profile_data.get("exchange") or "",
                "industry": profile_data.get(
                    "finnhubIndustry"
                ) or "",
                "ipo_date": profile_data.get("ipo") or "",
                "market_capitalization": profile_data.get(
                    "marketCapitalization"
                ) or 0,
                "website": profile_data.get("weburl") or "",
                "logo": profile_data.get("logo") or "",
            },
            "market_data": {
                "current_price": round(current_price, 2),
                "previous_close": round(previous_close, 2),
                "change_percent": round(change_percent, 2),
                "day_high": round(
                    float(quote_data.get("h") or 0),
                    2,
                ),
                "day_low": round(
                    float(quote_data.get("l") or 0),
                    2,
                ),
            },
            "news_analysis": {
                "articles_analyzed": len(processed_news),
                "positive_signals": positive_count,
                "negative_signals": negative_count,
                "news_score": news_score,
                "conclusion": news_conclusion,
                "articles": processed_news,
            },
            "preliminary_analysis": {
                "score": preliminary_score,
                "conclusion": preliminary_conclusion,
            },
            "data_source": "Finnhub",
            "important_notice": (
                "这是初步研究结果，目前主要依据实时价格、"
                "公司资料和新闻关键词，不构成投资建议。"
            ),
        }

    except HTTPException:
        raise

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接 Finnhub：{error}",
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"生成研究报告失败：{error}",
        ) from error

@app.get("/ai-research/{symbol}")
def get_ai_stock_research(
    symbol: str,
) -> dict[str, Any]:
    symbol = symbol.strip().upper()

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="请输入股票代码。",
        )

    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY 尚未设置。",
        )

    try:
        research_data = get_stock_research(symbol)

        news_articles = (
            research_data
            .get("news_analysis", {})
            .get("articles", [])
        )

        simplified_news = []

        for article in news_articles[:8]:
            simplified_news.append(
                {
                    "headline": article.get(
                        "headline",
                        "",
                    ),
                    "summary": article.get(
                        "summary",
                        "",
                    ),
                    "source": article.get(
                        "source",
                        "",
                    ),
                    "keyword_sentiment": article.get(
                        "sentiment",
                        "中性",
                    ),
                }
            )

        analysis_input = {
            "symbol": symbol,
            "company": research_data.get(
                "company",
                {},
            ),
            "market_data": research_data.get(
                "market_data",
                {},
            ),
            "news_summary": {
                "news_score": (
                    research_data
                    .get("news_analysis", {})
                    .get("news_score", 50)
                ),
                "conclusion": (
                    research_data
                    .get("news_analysis", {})
                    .get("conclusion", "")
                ),
                "articles": simplified_news,
            },
            "preliminary_analysis": (
                research_data.get(
                    "preliminary_analysis",
                    {},
                )
            ),
        }

        client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "http://localhost:8000",
                "X-OpenRouter-Title": "StockPilot AI Pro",
            },
        )

        completion = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一名谨慎、客观的美股研究分析助手。"
                        "请只根据用户提供的数据进行分析。"
                        "新闻中可能混入与目标股票无关的内容，"
                        "你必须主动识别并忽略不相关信息。"
                        "不得保证收益，不得声称可以预测准确的最佳买卖点。"
                        "所有价格只能表达为观察区间或风险参考。"
                        "请使用简体中文，表达清楚，适合投资初学者阅读。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请分析下面这只股票的数据，并生成一份结构化中文报告。\n\n"
                        f"{analysis_input}\n\n"
                        "报告必须包含以下部分：\n"
                        "1. 一句话结论\n"
                        "2. 公司与行业概况\n"
                        "3. 当前股价表现\n"
                        "4. 真正与该股票相关的重要新闻\n"
                        "5. 支持继续关注的理由\n"
                        "6. 反方观点与主要风险\n"
                        "7. 当前是否适合追高\n"
                        "8. 后续需要等待哪些确认信号\n"
                        "9. 可信度，使用低、中、高表示\n"
                        "10. 明确说明本报告不构成投资建议\n\n"
                        "不要虚构财务数字、技术指标或新闻。"
                        "数据中没有的信息请明确写“当前数据不足”。"
                    ),
                },
            ],
            temperature=0.2,
        )

        ai_report = (
            completion
            .choices[0]
            .message
            .content
            or ""
        )

        return {
            "symbol": symbol,
            "provider": "OpenRouter",
            "model": OPENROUTER_MODEL,
            "ai_report": ai_report,
            "source_data": analysis_input,
            "important_notice": (
                "AI报告可能存在错误，仅用于研究学习，"
                "不构成投资建议或收益保证。"
            ),
        }

    except HTTPException:
        raise

    except Exception as error:
        error_message = str(error)

        if "401" in error_message:
            detail = (
                "OpenRouter API Key 无效，"
                "请检查 backend/.env。"
            )
        elif "402" in error_message:
            detail = (
                "OpenRouter 余额不足，"
                "请充值或改用免费模型。"
            )
        elif "429" in error_message:
            detail = (
                "OpenRouter 请求次数过多，"
                "请稍后重试。"
            )
        else:
            detail = (
                f"OpenRouter AI分析失败：{error_message}"
            )

        raise HTTPException(
            status_code=500,
            detail=detail,
        ) from error

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_FILE = PROJECT_ROOT / "frontend" / "index.html"


@app.get("/app", include_in_schema=False)
def stockpilot_web_app():
    if not FRONTEND_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="找不到 frontend/index.html。",
        )

    return FileResponse(FRONTEND_FILE)

@app.get("/decision/{symbol}")
def get_stock_decision(
    symbol: str,
) -> dict[str, Any]:
    try:
        return build_decision(symbol)

    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"生成价格决策失败：{error}",
        ) from error