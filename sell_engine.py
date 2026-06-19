from __future__ import annotations
import re
import feedparser
import pandas as pd
from .indicators import technical_score_100
from market.data import get_price, company_name

AI_VALUE_CHAIN = {
    "NVDA": 95, "AMD": 82, "AVGO": 88, "MU": 86, "ARM": 78, "TSM": 85, "ASML": 84,
    "MSFT": 80, "GOOGL": 75, "AMZN": 72, "ORCL": 70, "DELL": 65, "PLTR": 78, "TSLA": 78,
    "INTC": 45, "SOXL": 75
}

POS_WORDS = ["beat", "beats", "upgrade", "raises", "growth", "surge", "strong", "record", "AI", "demand", "outperform"]
NEG_WORDS = ["miss", "downgrade", "cuts", "weak", "lawsuit", "probe", "delay", "slump", "risk", "tariff", "sell"]


def news_score(ticker: str) -> tuple[float, list[str]]:
    q = f"{ticker} stock"
    url = "https://news.google.com/rss/search?q=" + q.replace(" ", "+") + "&hl=en-US&gl=US&ceid=US:en"
    try:
        feed = feedparser.parse(url)
        titles = [e.get("title", "") for e in feed.entries[:8]]
    except Exception:
        titles = []
    text = " ".join(titles).lower()
    pos = sum(text.count(w.lower()) for w in POS_WORDS)
    neg = sum(text.count(w.lower()) for w in NEG_WORDS)
    score = 50 + min(30, pos * 6) - min(30, neg * 8)
    return float(max(0, min(100, score))), titles[:5]


def target_score(ticker: str, price: float | None) -> float:
    if not price:
        return 50.0
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
        if not target:
            return 50.0
        upside = (float(target) / float(price) - 1) * 100
        return float(max(0, min(100, 50 + upside)))
    except Exception:
        return 50.0


def money_flow_score(df: pd.DataFrame) -> float:
    if df is None or df.empty or "Volume" not in df.columns:
        return 50.0
    try:
        d = df.copy()
        vr = d["Volume"].tail(5).mean() / d["Volume"].tail(30).mean()
        price_chg = d["Close"].iloc[-1] / d["Close"].iloc[-6] - 1 if len(d) > 6 else 0
        score = 50 + (vr - 1) * 20 + price_chg * 100
        return float(max(0, min(100, round(score, 1))))
    except Exception:
        return 50.0


def conviction_for(ticker: str) -> dict:
    t = str(ticker).strip().upper()
    df = get_price(t, "6mo", "1d", ttl=1800)
    tech, reasons = technical_score_100(df)
    price = float(df["Close"].dropna().iloc[-1]) if df is not None and not df.empty else None
    nscore, headlines = news_score(t)
    tscore = target_score(t, price)
    chain = float(AI_VALUE_CHAIN.get(t, 45))
    flow = money_flow_score(df)
    total = round(nscore*0.20 + tscore*0.15 + tech*0.25 + chain*0.20 + flow*0.20, 1)
    if total >= 80: opinion = "추가매수"
    elif total >= 68: opinion = "유지"
    elif total >= 58: opinion = "관망"
    elif total >= 48: opinion = "비중축소"
    else: opinion = "매도검토"
    return {
        "티커": t, "종목명": company_name(t), "AI Conviction Score": total, "의견": opinion,
        "최근6시간뉴스": round(nscore,1), "목표주가": round(tscore,1), "기술추세": round(tech,1),
        "AI밸류체인": round(chain,1), "자금유입": round(flow,1),
        "핵심요약": " / ".join(reasons[:5]), "헤드라인": headlines
    }
