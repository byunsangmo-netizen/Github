from __future__ import annotations
import os, json, sqlite3, datetime as dt, time, hashlib, re, urllib.parse
from typing import List, Dict

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup



# ----------------------------- Self-contained V13 modules -----------------------------
CACHE_DIR = "cache_prices"
os.makedirs(CACHE_DIR, exist_ok=True)

NAME_FALLBACK = {
    "AAPL":"Apple", "MSFT":"Microsoft", "NVDA":"NVIDIA", "AMZN":"Amazon", "GOOGL":"Alphabet Class A",
    "GOOG":"Alphabet Class C", "META":"Meta Platforms", "TSLA":"Tesla", "AVGO":"Broadcom", "AMD":"Advanced Micro Devices",
    "MU":"Micron Technology", "NFLX":"Netflix", "ORCL":"Oracle", "ARM":"Arm Holdings", "INTC":"Intel",
    "SMH":"VanEck Semiconductor ETF", "XLK":"Technology Select Sector SPDR", "XBI":"SPDR S&P Biotech ETF",
    "BOTZ":"Global X Robotics & AI ETF", "ROBO":"ROBO Global Robotics & Automation ETF"
}

UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","AVGO","AMD","NFLX","ORCL","COST","PLTR","ADBE",
    "CRM","CSCO","INTC","MU","QCOM","AMAT","LRCX","KLAC","TXN","ARM","SMCI","PANW","CRWD","NOW","SHOP",
    "UBER","ABNB","MELI","PYPL","SBUX","PEP","COST","TMUS","CMCSA","INTU","ADP","ISRG","VRTX","REGN",
    "LLY","NVO","MRNA","PFE","JNJ","UNH","TMO","DHR","ABT","ABBV","GILD","AMGN","BIIB","ROKU","SNOW",
    "DDOG","NET","MDB","ZS","DELL","HPQ","WMT","HD","LOW","JPM","BAC","GS","MS","V","MA","AXP","XOM","CVX",
    "COP","SLB","GE","CAT","DE","HON","RTX","LMT","NOC","ROK","SYM","TER","IRBT"
]

SECTOR_ETFS = {
    "S&P500":"SPY", "NASDAQ":"QQQ", "반도체":"SMH", "기술":"XLK", "AI·소프트웨어":"IGV",
    "바이오·제약·의료":"XBI", "헬스케어":"XLV", "로봇":"BOTZ", "금융":"XLF", "에너지":"XLE",
    "산업재":"XLI", "소비재":"XLY", "필수소비":"XLP"
}

DEFAULT_REAL_PORTFOLIO = [
    ("MU", "Micron Technology"),
    ("NVDA", "NVIDIA"),
    ("AMD", "Advanced Micro Devices"),
    ("TSLA", "Tesla"),
    ("ORCL", "Oracle"),
    ("SPACEX", "SpaceX · 비상장 참고")
]


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Close"])
    df["EMA8"] = df["Close"].ewm(span=8, adjust=False).mean()
    df["EMA13"] = df["Close"].ewm(span=13, adjust=False).mean()
    df["MA21"] = df["Close"].rolling(21).mean()
    df["MA55"] = df["Close"].rolling(55).mean()
    df["RSI14"] = rsi(df["Close"], 14)
    if "Volume" in df.columns:
        df["VOL_MA20"] = df["Volume"].rolling(20).mean()
        df["VOL_RATIO"] = df["Volume"] / df["VOL_MA20"].replace(0, np.nan)
        direction = np.sign(df["Close"].diff()).fillna(0)
        df["OBV"] = (direction * df["Volume"].fillna(0)).cumsum()
        df["OBV_MA10"] = df["OBV"].rolling(10).mean()
    return df

def add_hhll(df: pd.DataFrame, hh: int = 20, ll: int = 10) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["HH20"] = out["High"].rolling(hh).max().shift(1)
    out["LL10"] = out["Low"].rolling(ll).min().shift(1)
    out["HH20_BREAK"] = out["Close"] > out["HH20"]
    out["LL10_BREAK"] = out["Close"] < out["LL10"]
    return out

def latest_technical_score(df: pd.DataFrame) -> tuple[float, list[str]]:
    if df is None or df.empty or len(df.dropna()) < 60:
        return 0.0, ["가격 데이터가 부족합니다."]
    d = enrich(df).dropna()
    if d.empty:
        return 0.0, ["지표 계산 데이터가 부족합니다."]
    last = d.iloc[-1]
    score = 0.0; reasons=[]
    if last["Close"] > last["MA21"]: score += 1.0; reasons.append("종가가 MA21 위")
    else: score -= 0.7; reasons.append("종가가 MA21 아래")
    if last["EMA8"] > last["EMA13"]: score += 0.8; reasons.append("EMA8이 EMA13 위")
    else: score -= 0.5; reasons.append("EMA8이 EMA13 아래")
    if last["MA21"] > last["MA55"]: score += 1.0; reasons.append("MA21이 MA55 위")
    else: score -= 0.7; reasons.append("MA21이 MA55 아래")
    if 45 <= float(last.get("RSI14", 50)) <= 68:
        score += 0.7; reasons.append(f"RSI {last['RSI14']:.1f}: 양호")
    elif float(last.get("RSI14", 50)) > 75:
        score -= 0.3; reasons.append(f"RSI {last['RSI14']:.1f}: 단기 과열")
    elif float(last.get("RSI14", 50)) < 40:
        score -= 0.5; reasons.append(f"RSI {last['RSI14']:.1f}: 약세")
    if "VOL_RATIO" in d.columns and pd.notna(last.get("VOL_RATIO")):
        vr = float(last["VOL_RATIO"])
        if vr >= 1.8: score += 0.8; reasons.append(f"거래량 {vr:.1f}배")
        elif vr >= 1.2: score += 0.3; reasons.append(f"거래량 {vr:.1f}배")
    return round(score, 2), reasons

def sell_signal(df_daily: pd.DataFrame, df_hhll: pd.DataFrame | None = None, buy_price: float | None = None) -> dict:
    if df_daily is None or df_daily.empty or len(df_daily.dropna()) < 60:
        return {"의견": "관찰", "점수": 0.0, "근거": "일봉 데이터가 부족합니다.", "손절참고": None, "목표참고": None}
    d = enrich(df_daily).dropna(); h = add_hhll(df_hhll if df_hhll is not None and not df_hhll.empty else df_daily).dropna()
    last = d.iloc[-1]; price = float(last["Close"]); score = 0.0; reasons=[]
    if price < float(last["MA55"]): score -= 2.0; reasons.append("종가가 MA55 아래로 하락")
    if float(last["EMA8"]) < float(last["EMA13"]): score -= 1.0; reasons.append("EMA8이 EMA13 아래로 약화")
    if float(last.get("RSI14", 50)) < 42: score -= 0.8; reasons.append(f"RSI {last['RSI14']:.1f}: 약세")
    if float(last.get("RSI14", 50)) > 75: score -= 0.4; reasons.append(f"RSI {last['RSI14']:.1f}: 과열 후 조정 주의")
    if not h.empty:
        hh = h.iloc[-1]
        if bool(hh.get("LL10_BREAK", False)): score -= 2.0; reasons.append("LL10 이탈: 터틀 기준 위험 신호")
        if bool(hh.get("HH20_BREAK", False)): score += 1.2; reasons.append("HH20 돌파: 추세 유지 신호")
    if price > float(last["EMA8"]) and float(last["EMA8"]) > float(last["EMA13"]):
        score += 1.0; reasons.append("가격이 EMA8 위이고 단기 추세 양호")
    if buy_price and buy_price > 0:
        pnl = (price / float(buy_price) - 1) * 100
        if pnl >= 20: score -= 0.4; reasons.append(f"수익률 {pnl:.1f}%: 일부 이익실현 검토")
        elif pnl <= -7: score -= 1.0; reasons.append(f"수익률 {pnl:.1f}%: 손실 관리 필요")
    if score <= -2.5: opinion = "매도 검토"
    elif score <= -0.8: opinion = "부분매도/비중축소"
    else: opinion = "유지"
    stop_ref = round(float(h["LL10"].iloc[-1]), 2) if not h.empty and pd.notna(h["LL10"].iloc[-1]) else round(price*0.94, 2)
    target_ref = round(price + (price - stop_ref) * 2, 2) if stop_ref < price else None
    return {"의견": opinion, "점수": round(score, 2), "현재가": round(price, 2), "손절참고": stop_ref, "목표참고": target_ref, "근거": " / ".join(reasons[:6])}



def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if df is None or df.empty or not all(c in df.columns for c in ["High", "Low", "Close"]):
        return pd.Series(dtype=float)
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def v14_position_decision(df_daily: pd.DataFrame, df_hhll: pd.DataFrame | None = None, buy_price: float | None = None, max_price: float | None = None) -> dict:
    """100점 기반 5단계 보유/매도 판단 엔진."""
    if df_daily is None or df_daily.empty or len(df_daily.dropna()) < 60:
        return {"AI의견":"관찰", "등급":"C", "종합점수":50, "핵심근거":"데이터 부족", "체크리스트":[], "현재가":0, "ATR손절":None, "추적손절":None, "터틀손절":None}
    d = enrich(df_daily).copy().dropna()
    if d.empty:
        return {"AI의견":"관찰", "등급":"C", "종합점수":50, "핵심근거":"지표 계산 데이터 부족", "체크리스트":[], "현재가":0, "ATR손절":None, "추적손절":None, "터틀손절":None}
    h = add_hhll(df_hhll if df_hhll is not None and not df_hhll.empty else df_daily).dropna()
    last = d.iloc[-1]
    price = float(last["Close"])
    score = 50.0
    checks=[]
    def add(label, cond, pts_true, pts_false=0):
        nonlocal score, checks
        if cond:
            score += pts_true; checks.append(f"■ {label}")
        else:
            score += pts_false; checks.append(f"□ {label}")
    add("가격이 EMA8 위", price > float(last["EMA8"]), 8, -8)
    add("EMA8 > EMA13", float(last["EMA8"]) > float(last["EMA13"]), 8, -8)
    add("EMA13 > MA21", float(last["EMA13"]) > float(last["MA21"]), 8, -6)
    add("MA21 > MA55", float(last["MA21"]) > float(last["MA55"]), 10, -10)
    rsi_val=float(last.get("RSI14",50))
    add(f"RSI {rsi_val:.1f}: 과열/약세 아님", 45 <= rsi_val <= 70, 6, -5 if rsi_val < 40 or rsi_val > 78 else 0)
    vr=float(last.get("VOL_RATIO",0)) if pd.notna(last.get("VOL_RATIO",np.nan)) else 0
    add(f"거래량 {vr:.1f}배", vr >= 1.2, 5, -2)
    if not h.empty:
        hh = h.iloc[-1]
        add("HH20 돌파/유지", bool(hh.get("HH20_BREAK", False)) or price >= float(hh.get("HH20", price))*0.98, 10, -2)
        add("LL10 이탈 없음", not bool(hh.get("LL10_BREAK", False)), 8, -18)
        turtle_stop = round(float(hh["LL10"]),2) if pd.notna(hh.get("LL10")) else None
    else:
        turtle_stop=None
    if buy_price and buy_price > 0:
        pnl=(price/float(buy_price)-1)*100
        if pnl >= 30:
            score -= 5; checks.append(f"■ 수익률 {pnl:.1f}%: 일부 이익실현 후보")
        elif pnl <= -7:
            score -= 12; checks.append(f"■ 수익률 {pnl:.1f}%: 손실 관리 필요")
        else:
            checks.append(f"■ 수익률 {pnl:.1f}%: 정상 범위")
    d["ATR14"] = atr(d,14)
    atr_val = float(d["ATR14"].iloc[-1]) if pd.notna(d["ATR14"].iloc[-1]) else price*0.03
    atr_stop = round(price - atr_val*2, 2)
    if max_price and max_price > 0:
        trailing_stop = round(max_price * 0.92, 2)
    else:
        trailing_stop = round(d["Close"].tail(40).max() * 0.92, 2)
    # Stop breach penalties
    if turtle_stop and price < turtle_stop:
        score -= 20
    if price < trailing_stop:
        score -= 10; checks.append("■ 추적손절선 이탈")
    score=max(0,min(100,round(score,0)))
    if score >= 85:
        opinion="추가매수"; grade="AAA"
    elif score >= 70:
        opinion="유지"; grade="AA"
    elif score >= 55:
        opinion="비중축소"; grade="A"
    elif score >= 40:
        opinion="절반매도"; grade="B"
    else:
        opinion="전량매도 검토"; grade="C"
    key_reason=" / ".join(checks[:7])
    return {"AI의견":opinion,"등급":grade,"종합점수":score,"핵심근거":key_reason,"체크리스트":checks,"현재가":round(price,2),"ATR손절":atr_stop,"추적손절":trailing_stop,"터틀손절":turtle_stop}

def v14_briefing_rows() -> pd.DataFrame:
    h = holdings_df()
    if h.empty:
        return pd.DataFrame()
    rows=[]
    invested = h[h["실제투자"]==True]
    for _, r in invested.iterrows():
        t=r["티커"]
        daily=fetch_price(t, period="6mo", interval="1d", ttl_hours=6)
        intraday=fetch_price(t, period="1mo", interval="15m", ttl_hours=3)
        dec=v14_position_decision(daily, intraday, float(r.get("매수가") or 0), None)
        bp=float(r.get("매수가") or 0)
        current=latest_current_price(t, daily) or dec.get("현재가",0)
        dec["현재가"] = current
        pnl=(current/bp-1)*100 if current and bp else 0
        rows.append({"티커":t,"종목명":r.get("종목명") or get_us_name(t),"수익률%":round(pnl,2),"AI의견":dec["AI의견"],"등급":dec["등급"],"점수":dec["종합점수"],"현재가":dec["현재가"],"ATR손절":dec["ATR손절"],"추적손절":dec["추적손절"],"터틀손절":dec["터틀손절"],"핵심근거":dec["핵심근거"]})
    return pd.DataFrame(rows)

def _cache_path(ticker: str, period: str, interval: str) -> str:
    key = hashlib.md5(f"{ticker}_{period}_{interval}".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.csv")

def _read_cache(path: str, ttl_hours: int) -> pd.DataFrame:
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl_hours * 3600:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            return enrich(df)
    except Exception:
        pass
    return pd.DataFrame()

def fetch_price(ticker: str, period: str="6mo", interval: str="1d", ttl_hours: int=12, force: bool=False, quiet: bool=True) -> pd.DataFrame:
    ticker = str(ticker).upper().strip()
    path = _cache_path(ticker, period, interval)
    if not force:
        cached = _read_cache(path, ttl_hours)
        if not cached.empty:
            return cached
    try:
        time.sleep(0.25)
        df = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            return _read_cache(path, ttl_hours=24*30)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        if not df.empty:
            df.to_csv(path)
        return enrich(df)
    except Exception as e:
        if not quiet:
            st.warning(f"{ticker} 데이터 요청 실패: {e}")
        return _read_cache(path, ttl_hours=24*30)


def latest_current_price(ticker: str, fallback_df: pd.DataFrame | None = None) -> float:
    """검색/분석 시점에 최대한 가까운 현재가를 가져온다.
    우선 30분봉 최근값을 사용하고, 실패하면 전달된 일봉/캐시 데이터의 종가를 사용한다.
    """
    t = normalize_ticker(ticker) if 'normalize_ticker' in globals() else str(ticker).upper().strip()
    if t == "SPACEX":
        return 0.0
    intraday = fetch_price(t, period="5d", interval="30m", ttl_hours=0.25)
    if intraday is not None and not intraday.empty:
        try:
            return round(float(intraday.dropna()["Close"].iloc[-1]), 4)
        except Exception:
            pass
    if fallback_df is not None and not fallback_df.empty:
        try:
            return round(float(fallback_df.dropna()["Close"].iloc[-1]), 4)
        except Exception:
            pass
    return 0.0

@st.cache_data(ttl=86400, show_spinner=False)
def get_us_name(ticker: str) -> str:
    t = str(ticker).upper().strip()
    if t == "SPACEX": return "SpaceX · 비상장 참고"
    if t in NAME_FALLBACK: return NAME_FALLBACK[t]
    try:
        info2 = yf.Ticker(t).info
        return info2.get("longName") or info2.get("shortName") or t
    except Exception:
        return t

def _x_values(df: pd.DataFrame):
    return list(range(len(df)))

def _tick_text(df: pd.DataFrame, max_ticks: int = 8):
    if df.empty: return [], []
    step = max(len(df)//max_ticks, 1)
    vals = list(range(0, len(df), step))
    txt = [df.index[i].strftime("%m/%d %H:%M") if hasattr(df.index[i], "strftime") else str(df.index[i]) for i in vals]
    return vals, txt

def price_chart(df: pd.DataFrame, ticker: str, title_suffix: str=""):
    fig = go.Figure()
    if df is None or df.empty:
        fig.update_layout(title=f"{ticker} 데이터 없음")
        return fig
    x = _x_values(df)
    fig.add_trace(go.Candlestick(x=x, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="Price"))
    for col, name in [("EMA8","EMA8"),("EMA13","EMA13"),("MA21","MA21"),("MA55","MA55")]:
        if col in df.columns:
            fig.add_trace(go.Scatter(x=x, y=df[col], mode="lines", name=name))
    tv, tt = _tick_text(df); fig.update_xaxes(tickmode="array", tickvals=tv, ticktext=tt)
    fig.update_layout(title=f"{ticker} Chart {title_suffix}", height=520, xaxis_rangeslider_visible=False, margin=dict(l=20,r=20,t=50,b=20))
    return fig

def rsi_chart(df: pd.DataFrame, ticker: str):
    fig = go.Figure()
    if df is None or df.empty or "RSI14" not in df.columns: return fig
    x = _x_values(df)
    fig.add_trace(go.Scatter(x=x, y=df["RSI14"], mode="lines", name="RSI14"))
    fig.add_hline(y=70, line_dash="dash"); fig.add_hline(y=30, line_dash="dash")
    tv, tt = _tick_text(df); fig.update_xaxes(tickmode="array", tickvals=tv, ticktext=tt)
    fig.update_layout(title=f"{ticker} RSI", height=280, margin=dict(l=20,r=20,t=50,b=20))
    return fig

def hhll_chart(df: pd.DataFrame, ticker: str):
    d = add_hhll(df).dropna() if df is not None and not df.empty else pd.DataFrame()
    fig = go.Figure()
    if d.empty:
        fig.update_layout(title=f"{ticker} HHLL 데이터 없음")
        return fig
    x = list(range(len(d)))
    fig.add_trace(go.Candlestick(x=x, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="15분봉"))
    fig.add_trace(go.Scatter(x=x, y=d["HH20"], mode="lines", name="Highest High 20"))
    fig.add_trace(go.Scatter(x=x, y=d["LL10"], mode="lines", name="Lowest Low 10"))
    br = d[d["HH20_BREAK"]]
    if not br.empty:
        fig.add_trace(go.Scatter(x=[d.index.get_loc(i) for i in br.index], y=br["Close"], mode="markers", name="HH20 돌파", marker_symbol="triangle-up", marker_size=10))
    lb = d[d["LL10_BREAK"]]
    if not lb.empty:
        fig.add_trace(go.Scatter(x=[d.index.get_loc(i) for i in lb.index], y=lb["Close"], mode="markers", name="LL10 이탈", marker_symbol="triangle-down", marker_size=10))
    tv, tt = _tick_text(d); fig.update_xaxes(tickmode="array", tickvals=tv, ticktext=tt)
    fig.update_layout(title=f"{ticker} HHLL 터틀트레이딩 참고 차트", height=430, xaxis_rangeslider_visible=False, margin=dict(l=20,r=20,t=50,b=20))
    return fig

DB_PATH = "stock_agent_v143.db"

st.set_page_config(page_title="Kappy Investment OS V14.2", page_icon="📈", layout="wide")

CSS = """
<style>
html, body, [class*="css"] { font-size: 0.88rem; }
.block-container { padding-top: 1.4rem; }
.small-badge {border-radius:999px; padding:2px 8px; background:#eef2ff; display:inline-block; margin-left:6px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ----------------------------- AI Conviction / Outlook Summary -----------------------------
AI_VALUE_CHAIN = {
    "NVDA": (95, "AI 가속기·CUDA 생태계 핵심"), "AVGO": (88, "AI 네트워킹·ASIC·인프라 핵심"),
    "AMD": (82, "AI GPU 후발 추격 및 서버 CPU"), "ARM": (78, "AI 엣지·저전력 CPU IP"),
    "TSM": (94, "AI 반도체 파운드리 핵심"), "ASML": (90, "첨단 반도체 장비 독점적 위치"),
    "MU": (86, "HBM·AI 메모리 밸류체인"), "ORCL": (76, "AI 클라우드 인프라·데이터베이스"),
    "MSFT": (92, "AI 소프트웨어·클라우드 플랫폼"), "GOOGL": (85, "AI 모델·광고·클라우드"),
    "GOOG": (85, "AI 모델·광고·클라우드"), "META": (82, "AI 광고·오픈소스 모델"),
    "AMZN": (84, "AWS AI 인프라"), "PLTR": (80, "기업 AI 운영 플랫폼"),
    "AAPL": (65, "온디바이스 AI 생태계"), "TSLA": (78, "자율주행·로봇·AI 추론"),
    "SOXL": (75, "반도체 레버리지 ETF"),
}
POSITIVE_NEWS_WORDS = ["upgrade","outperform","beat","raise","raised","surge","record","growth","strong","partnership","contract","ai","demand","guidance","target raised","bullish","accelerate"]
NEGATIVE_NEWS_WORDS = ["downgrade","underperform","miss","cut","lowered","slump","weak","lawsuit","probe","tariff","delay","risk","bearish","concern","investigation","recall","guidance cut"]

def fetch_google_news_headlines(ticker: str, name: str = "", hours: int = 6, limit: int = 8) -> list[str]:
    query = f'{ticker} {name} stock when:{hours}h'.strip()
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en"
    try:
        import requests
        r = requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        return [item.findtext("title") or "" for item in root.findall(".//item")[:limit] if item.findtext("title")]
    except Exception:
        return []

def recent_news_score(headlines: list[str]) -> tuple[float, list[str]]:
    if not headlines:
        return 0.0, ["최근 6시간 뉴스 헤드라인 없음 또는 RSS 요청 실패"]
    txt = " ".join(headlines).lower()
    pos = sum(1 for w in POSITIVE_NEWS_WORDS if w in txt)
    neg = sum(1 for w in NEGATIVE_NEWS_WORDS if w in txt)
    score = max(min((pos - neg) * 10 + 50, 100), 0)
    return round(score, 1), [f"긍정 키워드 {pos}개", f"부정 키워드 {neg}개"]

def target_price_score(ticker: str, current: float | None = None) -> tuple[float, str]:
    try:
        info = yf.Ticker(ticker).info
        target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
        if current is None:
            current = info.get("currentPrice") or info.get("regularMarketPrice")
        if target and current and current > 0:
            upside = (float(target) / float(current) - 1) * 100
            sc = max(min((upside + 20) / 60 * 100, 100), 0)
            return round(sc, 1), f"목표가 여력 {upside:.1f}%"
    except Exception:
        pass
    return 50.0, "목표주가 데이터 부족: 중립"

def technical_trend_score_100(df: pd.DataFrame) -> tuple[float, str]:
    if df is None or df.empty:
        return 0.0, "기술 데이터 부족"
    sc, reasons = latest_technical_score(df)
    conv = max(min((sc + 3) / 8 * 100, 100), 0)
    return round(conv, 1), ", ".join(reasons[:4])

def ai_value_chain_score(ticker: str) -> tuple[float, str]:
    t = normalize_ticker(ticker)
    if t in AI_VALUE_CHAIN:
        return float(AI_VALUE_CHAIN[t][0]), AI_VALUE_CHAIN[t][1]
    return 45.0, "AI 밸류체인 직접 노출도 낮거나 미확인"

def money_flow_score(df: pd.DataFrame) -> tuple[float, str]:
    if df is None or df.empty:
        return 0.0, "자금유입 데이터 부족"
    d = enrich(df).dropna()
    if d.empty:
        return 0.0, "자금유입 지표 계산 부족"
    last = d.iloc[-1]
    score = 50.0; notes=[]
    vr = last.get("VOL_RATIO", np.nan)
    if pd.notna(vr):
        if vr >= 2.0: score += 25; notes.append(f"거래량 {vr:.1f}배")
        elif vr >= 1.2: score += 12; notes.append(f"거래량 {vr:.1f}배")
        elif vr < 0.7: score -= 8; notes.append(f"거래량 {vr:.1f}배: 약함")
    if "OBV" in d.columns and "OBV_MA10" in d.columns and pd.notna(last.get("OBV_MA10")):
        if last["OBV"] > last["OBV_MA10"]:
            score += 15; notes.append("OBV가 10일 평균 위")
        else:
            score -= 10; notes.append("OBV가 10일 평균 아래")
    return round(max(min(score,100),0),1), ", ".join(notes) if notes else "중립"

def ai_conviction_row(ticker: str) -> dict:
    t = normalize_ticker(ticker)
    name = get_us_name(t)
    df = fetch_price(t, period="6mo", interval="1d", ttl_hours=6, force=False)
    current = float(df.dropna().iloc[-1]["Close"]) if df is not None and not df.empty else None
    headlines = fetch_google_news_headlines(t, name, hours=6, limit=8)
    news_sc, news_note = recent_news_score(headlines)
    target_sc, target_note = target_price_score(t, current)
    tech_sc, tech_note = technical_trend_score_100(df)
    chain_sc, chain_note = ai_value_chain_score(t)
    flow_sc, flow_note = money_flow_score(df)
    total = news_sc*0.22 + target_sc*0.18 + tech_sc*0.25 + chain_sc*0.20 + flow_sc*0.15
    opinion = "강한 긍정" if total >= 80 else "긍정" if total >= 65 else "중립" if total >= 50 else "주의" if total >= 35 else "부정"
    return {"티커": t, "종목명": name, "AI Conviction Score": round(total,1), "의견": opinion,
            "최근6시간뉴스": news_sc, "목표주가": target_sc, "기술추세": tech_sc, "AI밸류체인": chain_sc, "자금유입": flow_sc,
            "핵심요약": f"뉴스: {'; '.join(news_note[:2])} / 목표가: {target_note} / 기술: {tech_note} / AI: {chain_note} / 자금: {flow_note}",
            "주요헤드라인": " | ".join(headlines[:5]) if headlines else "최근 6시간 헤드라인 없음"}

# ----------------------------- DB -----------------------------
def con():
    return sqlite3.connect(DB_PATH)

def init_db():
    c = con(); cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS watchlist(
        ticker TEXT PRIMARY KEY, name TEXT, group_name TEXT DEFAULT '기본 관심그룹', updated_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS portfolio_holdings(
        ticker TEXT PRIMARY KEY,
        name TEXT,
        invested INTEGER DEFAULT 0,
        buy_price REAL DEFAULT 0,
        buy_amount REAL DEFAULT 0,
        quantity REAL DEFAULT 0,
        source TEXT DEFAULT '',
        memo TEXT DEFAULT '',
        updated_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS trade_journal(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT, name TEXT, side TEXT, entry REAL, stop REAL, target REAL, quantity REAL,
        status TEXT DEFAULT 'OPEN', exit_price REAL, note TEXT, opened_at TEXT, closed_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS recommendation_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT, name TEXT, source TEXT, score REAL, entry REAL,
        recommended_at TEXT, horizon_days INTEGER DEFAULT 30, checked_at TEXT,
        current_price REAL, return_pct REAL, status TEXT DEFAULT 'OPEN', note TEXT
    )""")
    c.commit(); c.close()

def q(sql, params=(), fetch=False):
    c = con(); cur = c.cursor(); cur.execute(sql, params)
    rows = cur.fetchall() if fetch else None
    c.commit(); c.close(); return rows

def now(): return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

init_db()

# ----------------------------- helpers -----------------------------
def normalize_ticker(t: str) -> str:
    return str(t or "").strip().upper()


ISIN_TO_TICKER = {
    "US0079031078":"AMD", "US0420682058":"ARM", "US11135F1012":"AVGO", "US24703L2025":"DELL",
    "US25459W4583":"SOXL", "US4581401001":"INTC", "US5951121038":"MU", "US88160R1014":"TSLA",
    "US67066G1040":"NVDA", "US68389X1054":"ORCL", "US0378331005":"AAPL", "US5949181045":"MSFT",
    "US30303M1027":"META", "US02079K3059":"GOOGL", "US02079K1079":"GOOG", "US0231351067":"AMZN",
}
NAME_TO_TICKER = {
    "마이크론":"MU", "엔비디아":"NVDA", "테슬라":"TSLA", "브로드컴":"AVGO", "암 홀딩스":"ARM",
    "어드밴스드":"AMD", "AMD":"AMD", "인텔":"INTC", "오라클":"ORCL", "델테크놀로지":"DELL",
    "디렉시온 반도체":"SOXL", "삼성전자":"005930.KS",
}

def ticker_from_nh_row(name: str, code: str) -> str:
    code = str(code or "").strip().upper()
    name = str(name or "").strip()
    if code in ISIN_TO_TICKER:
        return ISIN_TO_TICKER[code]
    if re.fullmatch(r"\d{6}", code):
        return code + ".KS"
    for k, v in NAME_TO_TICKER.items():
        if k.lower() in name.lower():
            return v
    return code

def _decode_uploaded_html(uploaded_file) -> str:
    """NH 나무증권이 .xls 확장자로 내려주는 HTML 파일을 안정적으로 문자열화합니다."""
    try:
        uploaded_file.seek(0)
        raw = uploaded_file.read()
    except Exception:
        raw = uploaded_file
    if isinstance(raw, str):
        return raw
    for enc in ["euc-kr", "cp949", "utf-8", "latin1"]:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def _html_tables_bs4(html: str) -> List[pd.DataFrame]:
    """pandas.read_html/lxml 없이 BeautifulSoup만으로 HTML table을 DataFrame으로 변환합니다."""
    soup = BeautifulSoup(html, "html.parser")
    tables: List[pd.DataFrame] = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells and any(str(x).strip() for x in cells):
                rows.append(cells)
        if not rows:
            continue
        max_len = max(len(r) for r in rows)
        rows = [r + [""] * (max_len - len(r)) for r in rows]
        # 첫 줄이 헤더일 가능성이 높으면 헤더로 사용합니다.
        header = rows[0]
        data = rows[1:] if len(rows) > 1 else []
        if len(set(header)) == len(header) and any(h in header for h in ["잔고유형", "상품명", "상품코드", "수량", "매입금액"]):
            tables.append(pd.DataFrame(data, columns=header))
        else:
            tables.append(pd.DataFrame(rows))
    return tables


def parse_nh_balance(uploaded_file) -> pd.DataFrame:
    """NH 나무증권 종합잔고 HTML .xls 파일을 보유종목 형식으로 변환합니다.

    V14.4 효율판: pandas.read_html(lxml 의존)을 쓰지 않고 BeautifulSoup로 직접 파싱합니다.
    Streamlit Cloud에서 lxml 누락으로 생기는 ImportError를 방지합니다.
    """
    html = _decode_uploaded_html(uploaded_file)
    tables = _html_tables_bs4(html)
    if not tables:
        return pd.DataFrame()

    df = None
    needed = {"잔고유형", "상품명", "상품코드", "수량", "매입금액"}
    for cand in tables:
        cols = set(map(str, cand.columns))
        if needed.issubset(cols):
            df = cand.copy()
            break
    if df is None:
        return pd.DataFrame()

    # 외화 환율 추정: 외화예수금 USD 행 또는 현재가가 환율로 들어오는 경우가 많습니다.
    fx = 1.0
    try:
        usd_rows = df[df["상품코드"].astype(str).str.upper().eq("USD")]
        if not usd_rows.empty:
            fx = _to_float(usd_rows.iloc[0].get("현재가"), 1.0) or 1.0
    except Exception:
        fx = 1.0

    rows=[]
    for _, r in df.iterrows():
        kind = str(r.get("잔고유형", ""))
        if not any(x in kind for x in ["주식", "외화주식", "외화ETP", "ETP"]):
            continue
        name = str(r.get("상품명", "")).strip()
        code = str(r.get("상품코드", "")).strip()
        if code.upper() == "USD" or not name:
            continue
        qty = _to_float(r.get("수량"), 0.0)
        buy_amount_krw = _to_float(r.get("매입금액"), 0.0)
        if qty <= 0 or buy_amount_krw <= 0:
            continue
        ticker = ticker_from_nh_row(name, code)
        is_foreign = str(code).upper().startswith("US") or "외화" in kind
        buy_price = buy_amount_krw / qty / (fx if is_foreign and fx > 0 else 1.0)
        # NH 파일의 현재가가 해외주식은 원화가 아니라 USD일 수 있으므로 매수가는 환율 추정 실패 시 보수적으로 원화로 남습니다.
        rows.append({
            "실제투자": True,
            "티커": ticker,
            "종목명": get_us_name(ticker) if not str(ticker).endswith((".KS", ".KQ")) else name,
            "매수가": round(buy_price, 4),
            "매수금액": round(buy_price * qty, 2),
            "수량": qty,
            "소스": "NH 나무증권 잔고",
            "메모": f"NH 원화매입금액 {buy_amount_krw:,.0f}원 / 환율 {fx:,.2f}" if is_foreign else "NH 국내주식 잔고",
        })
    return pd.DataFrame(rows)

def add_watchlist(ticker: str, group="기본 관심그룹"):
    t = normalize_ticker(ticker)
    if not t: return
    q("INSERT OR REPLACE INTO watchlist(ticker,name,group_name,updated_at) VALUES(?,?,?,?)", (t, get_us_name(t), group, now()))

def watchlist_df() -> pd.DataFrame:
    rows = q("SELECT ticker,name,group_name,updated_at FROM watchlist ORDER BY group_name,ticker", fetch=True) or []
    return pd.DataFrame(rows, columns=["티커","종목명","그룹","업데이트"])

def _to_float(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(str(v).replace(",", "").replace("원", "").replace("%", "").strip())
    except Exception:
        return default

def holdings_df() -> pd.DataFrame:
    rows = q("SELECT ticker,name,invested,buy_price,buy_amount,quantity,source,memo,updated_at FROM portfolio_holdings ORDER BY invested DESC,ticker", fetch=True) or []
    df = pd.DataFrame(rows, columns=["티커","종목명","실제투자","매수가","매수금액","수량","소스","메모","업데이트"])
    if not df.empty:
        df["실제투자"] = df["실제투자"].astype(bool)
        df["매수가"] = pd.to_numeric(df["매수가"], errors="coerce").fillna(0.0)
        df["수량"] = pd.to_numeric(df["수량"], errors="coerce").fillna(0.0)
        # V14.3: 매수가는 사용자가 입력하고, 매수금액은 매수가 × 수량으로 항상 자동 계산합니다.
        df["매수금액"] = (df["매수가"] * df["수량"]).round(2)
    return df

def save_holdings(df: pd.DataFrame):
    if df is None or df.empty: return
    for _, r in df.iterrows():
        t = normalize_ticker(r.get("티커"))
        if not t: continue
        name = r.get("종목명") or get_us_name(t)
        invested = 1 if bool(r.get("실제투자")) else 0
        buy_price = _to_float(r.get("매수가"), 0.0)
        qty = _to_float(r.get("수량"), 0.0)
        buy_amount = round(buy_price * qty, 2) if buy_price > 0 and qty > 0 else 0.0
        q("""INSERT OR REPLACE INTO portfolio_holdings
             (ticker,name,invested,buy_price,buy_amount,quantity,source,memo,updated_at)
             VALUES(?,?,?,?,?,?,?,?,?)""", (t, name, invested, buy_price, buy_amount, qty, r.get("소스", ""), r.get("메모", ""), now()))
        if invested:
            add_watchlist(t, "매수 종목")


def export_holdings_json() -> str:
    """현재 보유종목을 JSON 문자열로 내보냅니다."""
    df = holdings_df()
    payload = {
        "version": "V14.4",
        "exported_at": now(),
        "holdings": df.to_dict(orient="records") if df is not None and not df.empty else []
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def import_holdings_backup(uploaded_file, replace: bool = False) -> int:
    """V14.4 JSON/CSV 백업 파일을 보유종목 DB에 반영합니다."""
    if uploaded_file is None:
        return 0
    name = getattr(uploaded_file, "name", "").lower()
    uploaded_file.seek(0)
    if name.endswith(".json"):
        raw = uploaded_file.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig")
        payload = json.loads(raw)
        rows = payload.get("holdings", payload if isinstance(payload, list) else [])
        df = pd.DataFrame(rows)
    else:
        df = pd.read_csv(uploaded_file)
    if df.empty:
        return 0
    rename_map = {
        "ticker": "티커", "name": "종목명", "invested": "실제투자",
        "buy_price": "매수가", "buy_amount": "매수금액", "quantity": "수량",
        "source": "소스", "memo": "메모"
    }
    df = df.rename(columns={k:v for k,v in rename_map.items() if k in df.columns})
    for col in ["실제투자","티커","종목명","매수가","수량","소스","메모"]:
        if col not in df.columns:
            df[col] = False if col == "실제투자" else ""
    df["실제투자"] = df["실제투자"].apply(lambda x: str(x).lower() in ["true","1","yes","y","예","네"] if not isinstance(x, bool) else x)
    df["매수가"] = pd.to_numeric(df["매수가"], errors="coerce").fillna(0.0)
    df["수량"] = pd.to_numeric(df["수량"], errors="coerce").fillna(0.0)
    df["매수금액"] = (df["매수가"] * df["수량"]).round(2)
    if replace:
        q("DELETE FROM portfolio_holdings")
    save_holdings(df[["실제투자","티커","종목명","매수가","매수금액","수량","소스","메모"]])
    return len(df)

def technical_row(ticker: str, period="6mo", interval="1d") -> Dict:
    df = fetch_price(ticker, period=period, interval=interval, ttl_hours=12)
    if df.empty:
        return {"티커": ticker, "종목명": get_us_name(ticker), "점수": 0.0, "상태":"데이터부족"}
    sc, reasons = latest_technical_score(df)
    last = df.dropna().iloc[-1]
    return {
        "티커": ticker, "종목명": get_us_name(ticker), "점수": sc,
        "현재가": round(float(last["Close"]),2), "RSI": round(float(last.get("RSI14", 0)),1),
        "거래량배수": round(float(last.get("VOL_RATIO", 0)),2) if pd.notna(last.get("VOL_RATIO", np.nan)) else 0,
        "근거": " / ".join(reasons[:4])
    }

def market_regime() -> Dict:
    spy = fetch_price("SPY", period="6mo", interval="1d", ttl_hours=12)
    qqq = fetch_price("QQQ", period="6mo", interval="1d", ttl_hours=12)
    score = 0
    details = []
    for symbol, df in [("SPY", spy), ("QQQ", qqq)]:
        if not df.empty and len(df.dropna()) > 60:
            last = df.dropna().iloc[-1]
            if last["Close"] > last["MA55"]: score += 1; details.append(f"{symbol} MA55 위")
            else: score -= 1; details.append(f"{symbol} MA55 아래")
            if last["EMA8"] > last["EMA13"]: score += 0.5
            if last["RSI14"] < 35: score -= 0.5
            if last["RSI14"] > 70: score += 0.3
    if score >= 2.5: regime = "Risk On"
    elif score >= 1: regime = "Recovery"
    elif score > -1: regime = "Neutral"
    elif score > -2.5: regime = "Risk Off"
    else: regime = "Panic"
    cash = {"Risk On":10, "Recovery":20, "Neutral":35, "Risk Off":55, "Panic":75}.get(regime, 35)
    return {"시장체제": regime, "점수": round(score,2), "권장현금비중%": cash, "근거": " / ".join(details)}

def sector_rotation() -> pd.DataFrame:
    rows=[]
    for name, etf in SECTOR_ETFS.items():
        df = fetch_price(etf, period="3mo", interval="1d", ttl_hours=12)
        if df.empty or len(df.dropna()) < 30: continue
        d = df.dropna(); last=d.iloc[-1]
        mom20 = (last["Close"] / d["Close"].iloc[-21] - 1) * 100 if len(d) > 21 else 0
        sc = 0
        if last["Close"] > last["MA55"]: sc += 1.5
        if last["EMA8"] > last["EMA13"]: sc += 1.0
        sc += max(min(mom20/5, 2), -2)
        rows.append({"섹터":name,"대표ETF":etf,"섹터점수":round(sc,2),"20일수익률%":round(mom20,2)})
    return pd.DataFrame(rows).sort_values("섹터점수", ascending=False) if rows else pd.DataFrame()

def backtest_ticker(ticker: str) -> Dict:
    df = fetch_price(ticker, period="2y", interval="1d", ttl_hours=24)
    if df.empty or len(df.dropna()) < 120:
        return {"티커":ticker,"종목명":get_us_name(ticker),"검증점수":0,"거래수":0}
    d = add_hhll(enrich(df)).dropna()
    trades=[]
    for i in range(60, len(d)-10):
        row=d.iloc[i]; prev=d.iloc[i-1]
        signal = (prev["EMA8"] <= prev["MA55"] and row["EMA8"] > row["MA55"]) or bool(row.get("HH20_BREAK", False))
        if not signal: continue
        entry=float(row["Close"]); stop=float(row["LL10"]) if pd.notna(row["LL10"]) and row["LL10"] < entry else entry*0.94
        target=entry+(entry-stop)*2
        fut=d.iloc[i+1:i+11]
        ret=None
        for _, f in fut.iterrows():
            if float(f["Low"]) <= stop: ret=stop/entry-1; break
            if float(f["High"]) >= target: ret=target/entry-1; break
        if ret is None and not fut.empty: ret=float(fut["Close"].iloc[-1])/entry-1
        if ret is not None: trades.append(ret)
    if not trades:
        return {"티커":ticker,"종목명":get_us_name(ticker),"검증점수":0,"거래수":0}
    wins=[x for x in trades if x>0]; losses=[x for x in trades if x<=0]
    win=len(wins)/len(trades)*100
    avg=np.mean(trades)*100
    pf=(sum(wins)/abs(sum(losses))) if losses and abs(sum(losses))>0 else 9.99
    # simple MDD over trade equity
    eq=np.cumprod([1+x for x in trades]); peak=np.maximum.accumulate(eq); mdd=float(((eq/peak)-1).min()*100)
    score=win/10+avg+min(pf,4)
    return {"티커":ticker,"종목명":get_us_name(ticker),"거래수":len(trades),"승률%":round(win,1),"평균수익%":round(avg,2),"Profit Factor":round(pf,2),"MDD%":round(mdd,2),"검증점수":round(score,2)}

def build_portfolio_from_backtest(bt: pd.DataFrame, capital: float, risk_pct: float, n: int=5) -> pd.DataFrame:
    if bt is None or bt.empty: return pd.DataFrame()
    rows=[]
    for _, r in bt.sort_values("검증점수", ascending=False).head(n).iterrows():
        t=r["티커"]
        df=fetch_price(t, period="6mo", interval="1d", ttl_hours=12)
        if df.empty: continue
        h=add_hhll(df).dropna(); last=df.dropna().iloc[-1]
        entry=round(float(last["Close"]),2)
        stop=round(float(h["LL10"].iloc[-1]),2) if not h.empty and pd.notna(h["LL10"].iloc[-1]) else round(entry*0.94,2)
        target=round(entry+(entry-stop)*2,2) if stop < entry else round(entry*1.12,2)
        risk_amount=capital*risk_pct/100
        qty=int(risk_amount/(entry-stop)) if entry>stop else int((capital/n)/entry)
        amount=round(qty*entry,2)
        rows.append({"실제투자":False,"티커":t,"종목명":get_us_name(t),"점수":r.get("검증점수",0),"매수가":entry,"매수금액":amount,"수량":qty,"손절참고":stop,"목표참고":target,"소스":"백테스트 상위","메모":""})
    return pd.DataFrame(rows)

def analyze_holdings_current(df_holdings: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    if df_holdings is None or df_holdings.empty: return pd.DataFrame()
    for _, r in df_holdings[df_holdings["실제투자"]==True].iterrows():
        t=r["티커"]
        daily=fetch_price(t, period="6mo", interval="1d", ttl_hours=6)
        intraday=fetch_price(t, period="1mo", interval="15m", ttl_hours=3)
        dec=v14_position_decision(daily, intraday, float(r.get("매수가") or 0), None)
        current=latest_current_price(t, daily) or dec.get("현재가") or 0
        dec["현재가"] = current
        buy_price=float(r.get("매수가") or 0)
        amount=float(r.get("매수금액") or 0)
        pnl=(current/buy_price-1)*100 if current and buy_price else 0
        rows.append({"티커":t,"종목명":r.get("종목명") or get_us_name(t),"현재가":current,"매수가":buy_price,"매수금액":amount,"수익률%":round(pnl,2),"AI의견":dec["AI의견"],"등급":dec["등급"],"점수":dec["종합점수"],"ATR손절":dec["ATR손절"],"추적손절":dec["추적손절"],"터틀손절":dec["터틀손절"],"핵심근거":dec["핵심근거"]})
    return pd.DataFrame(rows)

# ----------------------------- Sidebar -----------------------------
st.sidebar.header("설정")
st.sidebar.caption("V14.4는 보유종목 저장/복원 백업 기능을 추가했습니다. Streamlit Cloud 재시작에도 JSON 백업으로 빠르게 복원할 수 있습니다.")
group_name=st.sidebar.text_input("관심그룹 이름", value="기본 관심그룹")
new_ticker=st.sidebar.text_input("관심 종목 추가", value="")
if st.sidebar.button("관심그룹에 추가") and new_ticker:
    add_watchlist(new_ticker, group_name); st.sidebar.success(f"{normalize_ticker(new_ticker)} 추가")

wdf=watchlist_df()
st.sidebar.markdown("---")
st.sidebar.subheader("관심그룹")
selected_from_sidebar=None
if not wdf.empty:
    for _, r in wdf.iterrows():
        if st.sidebar.button(f"{r['그룹']} · {r['티커']} · {r['종목명']}", key=f"wl_{r['티커']}"):
            selected_from_sidebar=r['티커']
    del_list=st.sidebar.multiselect("삭제할 종목 선택", wdf["티커"].tolist())
    if st.sidebar.button("선택 종목 삭제") and del_list:
        for t in del_list:
            q("DELETE FROM watchlist WHERE ticker=?", (t,))
        st.rerun()
else:
    st.sidebar.info("관심종목이 없습니다.")

# ----------------------------- Layout -----------------------------
st.title("📈 Kappy Investment OS V14.4 — 보유종목 저장·매도 엔진")
st.caption("앱을 켜면 보유 종목과 오늘 해야 할 일을 먼저 확인합니다. NH 잔고 파일을 가져오고, 데이터는 버튼을 눌렀을 때만 수집합니다.")

tabs = st.tabs(["오늘 브리핑", "보유종목", "종목 차트·에이전트", "후보 스캐너", "시장·섹터", "백테스트", "포트폴리오", "매매일지", "성과학습", "주식전망요약"])

# ----------------------------- Today briefing -----------------------------
with tabs[0]:
    st.subheader("오늘 해야 할 일")
    mr = market_regime()
    c1,c2,c3 = st.columns(3)
    c1.metric("시장 체제", mr["시장체제"], f"점수 {mr['점수']}")
    c2.metric("권장 현금비중", f"{mr['권장현금비중%']}%")
    c3.caption(mr.get("근거", ""))
    st.markdown("---")
    h = holdings_df()
    if h.empty or h[h["실제투자"]==True].empty:
        st.info("실제 보유 종목이 없습니다. 아래 버튼으로 기본 실제 투자 포트폴리오 모드를 시작하거나, '보유종목' 탭에서 직접 추가하세요.")
        if st.button("실제 투자 기본 포트폴리오 불러오기 · MU/NVDA/AMD/TSLA/ORCL/SpaceX", type="primary"):
            rows=[{"실제투자": True, "티커": t, "종목명": n, "매수가": 0.0, "매수금액": 0.0, "수량": 0.0, "소스": "기본 실제 투자 포트폴리오", "메모": "매수가와 수량을 입력하면 매수금액·손절·목표가 자동 계산됩니다."} for t,n in DEFAULT_REAL_PORTFOLIO]
            save_holdings(pd.DataFrame(rows))
            st.success("기본 실제 투자 포트폴리오를 추가했습니다. 보유종목 탭에서 매수금액과 수량만 입력하세요.")
            st.rerun()
    else:
        if st.button("오늘 보유종목 AI 브리핑 생성", type="primary"):
            st.session_state["v14_briefing"] = v14_briefing_rows()
        bdf = st.session_state.get("v14_briefing")
        if isinstance(bdf, pd.DataFrame) and not bdf.empty:
            buy_more = bdf[bdf["AI의견"].eq("추가매수")]
            keep = bdf[bdf["AI의견"].eq("유지")]
            reduce = bdf[bdf["AI의견"].isin(["비중축소", "절반매도", "전량매도 검토"])]
            a,b,c = st.columns(3)
            a.metric("추가매수 후보", len(buy_more))
            b.metric("유지", len(keep))
            c.metric("매도/축소 검토", len(reduce))
            st.dataframe(bdf.sort_values("점수", ascending=False), width="stretch", hide_index=True)
            st.markdown("### AI 요약")
            for _, r in bdf.sort_values("점수", ascending=False).iterrows():
                st.write(f"**{r['티커']} · {r['종목명']}**: {r['AI의견']} ({r['점수']:.0f}점, {r['등급']}) — {r['핵심근거']}")
        else:
            st.info("'오늘 보유종목 AI 브리핑 생성' 버튼을 누르세요.")

# ----------------------------- Holdings first -----------------------------
with tabs[1]:
    st.subheader("보유종목 관리")
    st.caption("실제 보유 종목을 계좌 중심으로 관리합니다. 체크된 종목은 관심그룹 '매수 종목'에 자동 등록됩니다.")
    with st.expander("💾 보유종목 저장/복원", expanded=True):
        st.caption("Streamlit Cloud는 재부팅·재배포 때 내부 DB가 초기화될 수 있습니다. 아래 JSON 백업을 받아두면 다음 접속 때 바로 복원할 수 있습니다.")
        backup_json = export_holdings_json()
        st.download_button(
            "보유종목 백업 다운로드(JSON)",
            data=backup_json.encode("utf-8-sig"),
            file_name=f"stock_agent_holdings_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json"
        )
        backup_file = st.file_uploader("보유종목 백업 복원(JSON/CSV)", type=["json", "csv"], key="holdings_backup_restore")
        replace_backup = st.checkbox("복원 시 기존 보유종목 전체 교체", value=False)
        if st.button("백업 파일 복원", type="secondary"):
            if backup_file is None:
                st.warning("복원할 JSON 또는 CSV 파일을 먼저 선택하세요.")
            else:
                try:
                    cnt = import_holdings_backup(backup_file, replace=replace_backup)
                    st.success(f"보유종목 {cnt}개를 복원했습니다.")
                    st.rerun()
                except Exception as e:
                    st.error(f"복원 실패: {e}")

    nh_file = st.file_uploader("NH 나무증권 종합잔고(.xls HTML) 가져오기", type=["xls", "html", "htm"], key="nh_balance_uploader")
    if nh_file is not None:
        imported = parse_nh_balance(nh_file)
        if imported.empty:
            st.warning("NH 잔고 파일에서 보유 종목을 찾지 못했습니다. 파일 형식을 확인해 주세요.")
        else:
            st.success(f"NH 잔고에서 {len(imported)}개 보유 종목을 읽었습니다.")
            st.dataframe(imported[[c for c in ["실제투자","티커","종목명","매수가","수량","매수금액","소스","메모"] if c in imported.columns]], width="stretch", hide_index=True)
            if st.button("NH 잔고를 보유종목에 반영", type="primary"):
                save_holdings(imported)
                st.success("NH 잔고를 저장했습니다. 실제투자 종목은 관심그룹 '매수 종목'에 등록됩니다.")
                st.rerun()
    hdf = holdings_df()
    if hdf.empty:
        hdf = pd.DataFrame(columns=["실제투자","티커","종목명","매수가","매수금액","수량","소스","메모"])
        if st.button("실제 투자 기본 포트폴리오 불러오기", type="primary", key="seed_holdings_tab"):
            rows=[{"실제투자": True, "티커": t, "종목명": n, "매수가": 0.0, "매수금액": 0.0, "수량": 0.0, "소스": "기본 실제 투자 포트폴리오", "메모": "매수가와 수량만 입력"} for t,n in DEFAULT_REAL_PORTFOLIO]
            save_holdings(pd.DataFrame(rows)); st.rerun()
    st.info("매수가와 수량만 입력하세요. 매수금액·손절참고·목표참고는 자동 계산됩니다.")
    edited = st.data_editor(
        hdf[[c for c in ["실제투자","티커","종목명","매수가","수량","소스","메모"] if c in hdf.columns]],
        width="stretch", hide_index=True, num_rows="dynamic", key="v14_holdings_editor",
        disabled=["종목명"],
        column_config={
            "매수가": st.column_config.NumberColumn("매수가", min_value=0.0, step=0.01),
            "수량": st.column_config.NumberColumn("수량", min_value=0.0, step=1.0),
        }
    )
    if edited is not None and not edited.empty:
        preview = edited[[c for c in ["티커","종목명","매수가","수량"] if c in edited.columns]].copy()
        preview["매수금액"] = (pd.to_numeric(preview["매수가"], errors="coerce").fillna(0) * pd.to_numeric(preview["수량"], errors="coerce").fillna(0)).round(2)
        st.caption("자동계산 매수금액 미리보기")
        st.dataframe(preview[[c for c in ["티커","종목명","매수가","수량","매수금액"] if c in preview.columns]], width="stretch", hide_index=True)
    c1,c2 = st.columns(2)
    if c1.button("보유종목 저장", type="primary"):
        save_holdings(edited)
        st.success("저장했습니다. 실제투자 체크 종목은 관심종목 '매수 종목'에 등록됩니다.")
        st.rerun()
    if c2.button("보유종목 현 상황 분석"):
        save_holdings(edited)
        st.session_state["v14_holding_analysis"] = v14_briefing_rows()
    adf = st.session_state.get("v14_holding_analysis")
    if isinstance(adf, pd.DataFrame) and not adf.empty:
        st.markdown("### 5단계 매도 엔진 결과")
        st.dataframe(adf.sort_values("점수", ascending=False), width="stretch", hide_index=True)
        sel = st.selectbox("차트로 확인할 보유 종목", adf["티커"].tolist())
        d = fetch_price(sel, period="6mo", interval="1d", ttl_hours=6)
        h = fetch_price(sel, period="1mo", interval="15m", ttl_hours=3)
        st.plotly_chart(price_chart(d, sel, "V14 매도 판단 참고"), width="stretch")
        st.plotly_chart(hhll_chart(h, sel), width="stretch")

# ----------------------------- Chart tab -----------------------------
with tabs[2]:
    c1,c2,c3=st.columns([1.4,1,1])
    default = selected_from_sidebar or st.session_state.get("selected_ticker", "TSLA")
    ticker = c1.text_input("티커 검색", value=default).upper().strip()
    preset = c2.selectbox("차트 기간", ["6mo","1mo","1wk","1d","30m"], index=0)
    run = c3.button("현 종목 분석", type="primary")
    if ticker:
        st.session_state["selected_ticker"] = ticker
    if run and ticker:
        period, interval = ("60d","30m") if preset=="30m" else (("1mo","30m") if preset=="1mo" else (("5d","30m") if preset=="1wk" else (("1d","30m") if preset=="1d" else (preset,"1d"))))
        df=fetch_price(ticker, period=period, interval=interval, ttl_hours=6, force=False, quiet=False)
        if df.empty:
            st.warning("가격 데이터를 가져오지 못했습니다. Yahoo 제한이면 잠시 후 다시 시도하세요.")
        else:
            left,right=st.columns([1.8,1])
            with left:
                st.plotly_chart(price_chart(df,ticker,f"({preset})"), width="stretch")
                st.plotly_chart(rsi_chart(df,ticker), width="stretch")
                hdf=fetch_price(ticker, period="1mo", interval="15m", ttl_hours=6)
                st.plotly_chart(hhll_chart(hdf,ticker), width="stretch")
            with right:
                sc,reasons=latest_technical_score(df)
                current=latest_current_price(ticker, df)
                opinion="매수" if sc>=2.5 else "매도" if sc<=-1.5 else "관망"
                st.subheader(opinion)
                st.metric("현재가 · 검색시점 기준", current if current else "확인불가")
                st.metric("기술점수", sc)
                st.write(f"핵심 기술 근거는 {', '.join(reasons[:5])}입니다.")
                st.caption("뉴스·옵션 데이터는 자동 요청하지 않습니다. 필요 시 후보 스캐너/시장 탭에서 별도 실행하세요.")
    else:
        st.info("티커 입력 후 '현 종목 분석'을 누르세요. 앱 시작 시 자동 다운로드하지 않습니다.")

# ----------------------------- Scanner -----------------------------
with tabs[3]:
    st.subheader("후보 스캐너")
    st.caption("Rate limit 방지를 위해 버튼을 눌렀을 때만 순차 분석합니다. 처음에는 20개 이하를 권장합니다.")
    c1,c2=st.columns(2)
    max_scan=c1.selectbox("분석 종목 수", [20,50,100], index=0)
    top_n=c2.selectbox("추천 표시 수", [20,50], index=0)
    if st.button("후보 분석 실행", type="primary"):
        rows=[]
        progress=st.progress(0)
        for i,t in enumerate(UNIVERSE[:int(max_scan)]):
            rows.append(technical_row(t, period="6mo", interval="1d"))
            progress.progress((i+1)/int(max_scan))
        scan=pd.DataFrame(rows).sort_values("점수", ascending=False).head(int(top_n))
        st.session_state["scan_df"]=scan
    sdf=st.session_state.get("scan_df")
    if isinstance(sdf,pd.DataFrame) and not sdf.empty:
        st.dataframe(sdf, width="stretch", hide_index=True)
    else:
        st.info("후보 분석 실행 버튼을 누르면 결과가 표시됩니다.")

# ----------------------------- Market -----------------------------
with tabs[4]:
    st.subheader("시장 체제·섹터 로테이션")
    if st.button("시장·섹터 분석 실행", type="primary"):
        st.session_state["market_regime"]=market_regime()
        st.session_state["sector_df"]=sector_rotation()
    if "market_regime" in st.session_state:
        st.json(st.session_state["market_regime"])
    sec=st.session_state.get("sector_df")
    if isinstance(sec,pd.DataFrame) and not sec.empty:
        st.dataframe(sec, width="stretch", hide_index=True)
    else:
        st.info("시장·섹터 분석 실행 버튼을 누르세요.")

# ----------------------------- Backtest -----------------------------
with tabs[5]:
    st.subheader("백테스트")
    c1,c2=st.columns(2)
    count=c1.selectbox("검증 종목 수", [20,50,100], index=0)
    source=c2.selectbox("소스", ["기본 유니버스", "후보 스캐너 상위"])
    if st.button("백테스트 실행", type="primary"):
        if source=="후보 스캐너 상위" and isinstance(st.session_state.get("scan_df"), pd.DataFrame):
            tickers=st.session_state["scan_df"]["티커"].tolist()
        else:
            tickers=UNIVERSE[:int(count)]
        rows=[]; prog=st.progress(0)
        for i,t in enumerate(tickers[:int(count)]):
            rows.append(backtest_ticker(t)); prog.progress((i+1)/min(len(tickers),int(count)))
        bt=pd.DataFrame(rows).sort_values("검증점수", ascending=False)
        st.session_state["bt_df"]=bt
    bt=st.session_state.get("bt_df")
    if isinstance(bt,pd.DataFrame) and not bt.empty:
        st.dataframe(bt, width="stretch", hide_index=True)
    else:
        st.info("백테스트 실행 버튼을 누르세요.")

# ----------------------------- Portfolio sell management -----------------------------
with tabs[6]:
    st.subheader("포트폴리오·매도 타이밍 관리")
    st.caption("백테스트 상위 5개로 포트폴리오를 만들고, 실제 투자한 종목을 체크하면 관심종목(매수 종목)에 자동 등록됩니다.")
    c1,c2,c3=st.columns(3)
    capital=c1.number_input("계좌 규모", min_value=1000.0, value=100000.0, step=1000.0)
    risk_pct=c2.number_input("1종목 위험률%", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
    npos=c3.selectbox("백테스트 상위 포트폴리오 수", [5,8,10], index=0)
    if st.button("백테스트 상위로 포트폴리오 만들기", type="primary"):
        bt=st.session_state.get("bt_df")
        if not isinstance(bt,pd.DataFrame) or bt.empty:
            st.warning("먼저 백테스트를 실행하세요.")
        else:
            plan=build_portfolio_from_backtest(bt, capital, risk_pct, int(npos))
            save_holdings(plan)
            st.session_state["portfolio_edit_df"]=holdings_df()
            st.success("포트폴리오 초안을 만들었습니다. 실제 투자한 종목을 체크하고 매수금액을 수정하세요.")
    hdf=holdings_df()
    if not hdf.empty:
        edit_cols=["실제투자","티커","종목명","매수가","수량","매수금액","손절참고","목표참고","소스","메모"]
        # merge optional stop/target from session plan if missing in DB display
        for col in ["손절참고","목표참고"]:
            if col not in hdf.columns: hdf[col]=0.0
        st.info("매수가와 수량만 수정하세요. 매수금액·손절참고·목표참고는 자동 계산됩니다.")
        edited=st.data_editor(
            hdf[[c for c in edit_cols if c in hdf.columns]], width="stretch", hide_index=True, num_rows="dynamic", key="holdings_editor",
            disabled=["종목명","매수금액","손절참고","목표참고"],
            column_config={
                "매수금액": st.column_config.NumberColumn("매수금액", min_value=0.0, step=100.0),
                "수량": st.column_config.NumberColumn("수량", min_value=0.0, step=1.0),
            }
        )
        if not hdf.empty:
            st.caption("자동계산 매수금액 미리보기")
            st.dataframe(hdf[[c for c in ["티커","종목명","매수금액","수량","매수가"] if c in hdf.columns]], width="stretch", hide_index=True)
        csave,canalyze=st.columns(2)
        if csave.button("포트폴리오 수정 저장"):
            save_holdings(edited)
            st.success("저장했습니다. 체크된 종목은 관심종목 '매수 종목'에 등록됩니다.")
            st.rerun()
        if canalyze.button("현 상황 분석: 유지/부분매도/매도", type="primary"):
            save_holdings(edited)
            st.session_state["sell_analysis"]=analyze_holdings_current(holdings_df())
        sadf=st.session_state.get("sell_analysis")
        if isinstance(sadf,pd.DataFrame) and not sadf.empty:
            st.markdown("### 보유 종목 매도 타이밍 제안")
            st.dataframe(sadf, width="stretch", hide_index=True)
            st.caption("유지/부분매도/매도는 EMA·MA55·RSI·HH20/LL10 기준의 참고 의견입니다.")
            sel=st.selectbox("차트로 확인할 보유 종목", sadf["티커"].tolist())
            d=fetch_price(sel, period="6mo", interval="1d", ttl_hours=6)
            h=fetch_price(sel, period="1mo", interval="15m", ttl_hours=3)
            st.plotly_chart(price_chart(d, sel, "매도 판단 참고"), width="stretch")
            st.plotly_chart(hhll_chart(h, sel), width="stretch")
    else:
        st.info("백테스트 상위 포트폴리오 만들기 버튼을 누르거나 아래에서 직접 보유 종목을 추가하세요.")
        with st.form("manual_hold"):
            c1,c2,c3,c4=st.columns(4)
            mt=c1.text_input("티커", value="TSLA").upper().strip()
            bp=c2.number_input("매수가", min_value=0.0, value=0.0, step=0.01)
            qty=c3.number_input("수량", min_value=0.0, value=0.0, step=1.0)
            inv=c4.checkbox("실제투자", value=True)
            if st.form_submit_button("보유 종목 추가") and mt:
                ba=bp*qty if bp>0 and qty>0 else 0
                save_holdings(pd.DataFrame([{"실제투자":inv,"티커":mt,"종목명":get_us_name(mt),"매수가":bp,"매수금액":ba,"수량":qty,"소스":"직접입력","메모":""}]))
                st.rerun()

# ----------------------------- Journal -----------------------------
with tabs[7]:
    st.subheader("매매일지")
    with st.form("journal"):
        c1,c2,c3,c4,c5,c6=st.columns(6)
        jt=c1.text_input("티커", value=st.session_state.get("selected_ticker","TSLA")).upper().strip()
        side=c2.selectbox("방향", ["BUY","SELL"])
        entry=c3.number_input("진입/청산가", min_value=0.0, value=0.0, step=0.01)
        stop=c4.number_input("손절가", min_value=0.0, value=0.0, step=0.01)
        target=c5.number_input("목표가", min_value=0.0, value=0.0, step=0.01)
        qty=c6.number_input("수량", min_value=0.0, value=0.0, step=1.0)
        note=st.text_input("메모")
        if st.form_submit_button("매매 기록 추가") and jt and entry>0 and qty>0:
            q("""INSERT INTO trade_journal(ticker,name,side,entry,stop,target,quantity,status,note,opened_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""", (jt,get_us_name(jt),side,entry,stop,target,qty,"OPEN",note,now()))
            st.success("기록했습니다.")
    rows=q("SELECT id,ticker,name,side,entry,stop,target,quantity,status,exit_price,note,opened_at,closed_at FROM trade_journal ORDER BY id DESC", fetch=True) or []
    jdf=pd.DataFrame(rows, columns=["id","티커","종목명","방향","진입가","손절가","목표가","수량","상태","청산가","메모","진입일","청산일"])
    if not jdf.empty:
        st.dataframe(jdf, width="stretch", hide_index=True)
    else:
        st.info("아직 기록된 매매가 없습니다.")

# ----------------------------- Learning -----------------------------
with tabs[8]:
    st.subheader("성과학습")
    st.caption("추천과 실제 매매 기록이 쌓이면 어떤 조건이 잘 맞았는지 확인합니다.")
    jrows=q("SELECT ticker,name,side,entry,quantity,exit_price,status,opened_at,closed_at FROM trade_journal", fetch=True) or []
    if jrows:
        df=pd.DataFrame(jrows, columns=["티커","종목명","방향","진입가","수량","청산가","상태","진입일","청산일"])
        closed=df[(df["상태"]=="CLOSED") & df["청산가"].notna()].copy()
        if not closed.empty:
            closed["손익률%"]=(closed["청산가"]/closed["진입가"]-1)*100
            st.metric("완료 거래 승률", f"{(closed['손익률%']>0).mean()*100:.1f}%")
            st.metric("평균 손익률", f"{closed['손익률%'].mean():.2f}%")
            st.dataframe(closed, width="stretch", hide_index=True)
        else:
            st.info("청산 완료된 거래가 아직 없습니다.")
    else:
        st.info("매매일지를 쌓으면 성과학습이 가능해집니다.")

# ----------------------------- Outlook Summary / AI Conviction -----------------------------
with tabs[9]:
    st.subheader("주식전망요약 · AI Conviction Score")
    st.markdown("""
이런 판단을 더 객관적으로 만들기 위해 **AI Conviction Score(확신도 점수)** 를 사용합니다.

매일 각 종목을 아래 5가지 항목으로 평가합니다.
- 📰 **최근 6시간 뉴스 점수**
- 💰 **기관 목표주가 변화/여력**
- 📈 **기술적 추세**
- 🌍 **AI 밸류체인 내 위치**
- 💵 **자금 유입 강도**

종합 점수는 100점 기준으로 계산합니다. 데이터 요청 과다를 막기 위해 버튼을 눌렀을 때만 실행됩니다.
""")
    base_tickers = []
    h = holdings_df()
    if not h.empty:
        base_tickers += h[h["실제투자"]==True]["티커"].dropna().astype(str).tolist()
    w = watchlist_df()
    if not w.empty:
        base_tickers += w["티커"].dropna().astype(str).tolist()
    base_tickers = list(dict.fromkeys([normalize_ticker(x) for x in base_tickers if x])) or ["MU","NVDA","AMD","TSLA","ORCL","AVGO","ARM"]
    tickers_text = st.text_area("분석할 종목", value=", ".join(base_tickers), height=80)
    if st.button("AI Conviction Score 분석 실행", type="primary"):
        tickers = [normalize_ticker(x) for x in re.split(r"[,\n\s]+", tickers_text) if normalize_ticker(x)]
        rows=[]
        prog=st.progress(0)
        for i,t in enumerate(tickers[:30]):
            rows.append(ai_conviction_row(t))
            prog.progress((i+1)/max(len(tickers[:30]),1))
        st.session_state["conviction_df"] = pd.DataFrame(rows).sort_values("AI Conviction Score", ascending=False) if rows else pd.DataFrame()
    cdf = st.session_state.get("conviction_df")
    if isinstance(cdf, pd.DataFrame) and not cdf.empty:
        st.dataframe(cdf[["티커","종목명","AI Conviction Score","의견","최근6시간뉴스","목표주가","기술추세","AI밸류체인","자금유입","핵심요약"]], width="stretch", hide_index=True)
        sel = st.selectbox("헤드라인 확인 종목", cdf["티커"].tolist())
        row = cdf[cdf["티커"]==sel].iloc[0]
        st.markdown("### 최근 확인된 주요 헤드라인")
        for hline in str(row.get("주요헤드라인", "")).split(" | "):
            if hline.strip():
                st.write("- " + hline.strip())
    else:
        st.info("분석 실행 버튼을 누르면 종목별 AI Conviction Score가 계산됩니다.")
