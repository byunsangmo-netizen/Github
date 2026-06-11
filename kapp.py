import os, json, sqlite3, textwrap, datetime as dt, time
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
import feedparser
import requests
from dotenv import load_dotenv
try:
    from pykrx import stock as krx_stock
except Exception:
    krx_stock = None

load_dotenv()

# 주요 한국 종목명 fallback: pykrx/yfinance가 실패해도 표에 최소한 회사명이 보이도록 합니다.
KR_NAME_FALLBACK = {
    "005930":"삼성전자", "000660":"SK하이닉스", "035420":"NAVER", "035720":"카카오",
    "005380":"현대차", "000270":"기아", "005490":"POSCO홀딩스", "051910":"LG화학",
    "373220":"LG에너지솔루션", "068270":"셀트리온", "207940":"삼성바이오로직스",
    "086520":"에코프로", "247540":"에코프로비엠", "066570":"LG전자", "096770":"SK이노베이션",
    "010130":"고려아연", "000250":"삼천당제약", "222800":"심텍", "034020":"두산에너빌리티",
    "065350":"신성델타테크", "357780":"솔브레인", "253450":"스튜디오드래곤", "121600":"나노신소재",
    "032830":"삼성생명", "005440":"현대지에프홀딩스", "028260":"삼성물산", "012330":"현대모비스",
    "003670":"포스코퓨처엠", "042700":"한미반도체", "196170":"알테오젠", "028300":"HLB",
    "277810":"레인보우로보틱스", "454910":"두산로보틱스", "090360":"로보스타", "058610":"에스피지"
}

DB_PATH = "kstock_agent.db"
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# ----------------------------- DB -----------------------------
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS watchlist(
        ticker TEXT PRIMARY KEY,
        name TEXT,
        group_name TEXT DEFAULT '기본 관심그룹',
        updated_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS analyses(
        ticker TEXT,
        trade_date TEXT,
        opinion TEXT,
        summary TEXT,
        score REAL,
        payload TEXT,
        created_at TEXT,
        PRIMARY KEY(ticker, trade_date)
    )""")
    con.commit(); con.close()

def db_execute(sql, params=(), fetch=False):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor(); cur.execute(sql, params)
    rows = cur.fetchall() if fetch else None
    con.commit(); con.close()
    return rows

# -------------------------- Indicators -------------------------
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # 기존 분석 호환용 단순이동평균선
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA120"] = df["Close"].rolling(120).mean()
    # 차트/스캐너 기준: 8·13은 지수이동평균선, 21·55는 단순이동평균선
    df["EMA8"] = df["Close"].ewm(span=8, adjust=False).mean()
    df["EMA13"] = df["Close"].ewm(span=13, adjust=False).mean()
    df["MA21"] = df["Close"].rolling(21).mean()
    df["MA55"] = df["Close"].rolling(55).mean()
    df["RSI14"] = rsi(df["Close"], 14)
    df["RET20"] = df["Close"].pct_change(20)
    df["VOL20"] = df["Close"].pct_change().rolling(20).std() * np.sqrt(252)
    df["VOL_MA20"] = df["Volume"].rolling(20).mean() if "Volume" in df.columns else np.nan
    df["VOL_RATIO"] = df["Volume"] / df["VOL_MA20"].replace(0, np.nan) if "Volume" in df.columns else np.nan
    if "Volume" in df.columns:
        direction = np.sign(df["Close"].diff()).fillna(0)
        df["OBV"] = (direction * df["Volume"].fillna(0)).cumsum()
        df["OBV_MA20"] = df["OBV"].rolling(20).mean()
    else:
        df["OBV"] = np.nan
        df["OBV_MA20"] = np.nan
    return df

def fetch_price(ticker: str, period="1y", interval="1d") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    return enrich(df)

def fetch_price_preset(ticker: str, preset="6mo") -> pd.DataFrame:
    # 메인 이동평균 차트 기간/봉 기준
    # 6개월 이상은 일봉, 1개월/1주일/하루는 30분봉으로 표시합니다.
    if preset == "30m":
        return fetch_price(ticker, period="60d", interval="30m")
    if preset == "1mo":
        return fetch_price(ticker, period="1mo", interval="30m")
    if preset == "1wk":
        return fetch_price(ticker, period="5d", interval="30m")
    if preset == "1d":
        return fetch_price(ticker, period="1d", interval="30m")
    return fetch_price(ticker, period=preset, interval="1d")

def fetch_hhll_price_preset(ticker: str, preset="1mo") -> pd.DataFrame:
    # 터틀트레이딩 참고용 HHLL 차트는 15분봉만 사용합니다.
    # Yahoo Finance는 15분봉 장기 데이터 제한이 있어 6개월 선택 시 우선 6mo로 시도하고, 실패하면 60d로 대체합니다.
    period_map = {"6mo": "6mo", "1mo": "1mo", "1wk": "5d", "1d": "1d"}
    period = period_map.get(preset, "1mo")
    df = fetch_price(ticker, period=period, interval="15m")
    if df.empty and preset == "6mo":
        df = fetch_price(ticker, period="60d", interval="15m")
    return df

def add_hhll(df: pd.DataFrame, highest_n: int = 20, lowest_n: int = 10) -> pd.DataFrame:
    d = df.copy()
    d["HH20"] = d["High"].rolling(highest_n).max()
    d["LL10"] = d["Low"].rolling(lowest_n).min()
    return d

def _is_intraday_index(index) -> bool:
    """일봉이 아닌 15분/30분봉 차트인지 판단합니다."""
    try:
        if len(index) < 2:
            return False
        idx = pd.DatetimeIndex(index)
        span = idx[1] - idx[0]
        return span < pd.Timedelta(days=1)
    except Exception:
        return False

def make_compact_x(df: pd.DataFrame):
    """거래 없는 야간/주말/휴장 공백을 없애기 위해 분봉 차트는 category 축 라벨을 사용합니다."""
    if df is None or df.empty:
        return []
    if _is_intraday_index(df.index):
        return [pd.Timestamp(x).strftime("%m-%d %H:%M") for x in df.index]
    return df.index

def turtle_metrics(df: pd.DataFrame, highest_n: int = 20, lowest_n: int = 10) -> Dict:
    """15분봉 HH20/LL10 기준 터틀 참고 점수. 돌파는 전봉 기준 HH/LL을 사용합니다."""
    if df is None or df.empty or len(df) < max(highest_n, lowest_n) + 3:
        return {"score": 0.0, "status": "HHLL 데이터 부족", "hh_breakout": False, "ll_breakdown": False}
    d = add_hhll(df, highest_n, lowest_n).dropna().copy()
    if len(d) < 3:
        return {"score": 0.0, "status": "HHLL 계산 데이터 부족", "hh_breakout": False, "ll_breakdown": False}
    last = d.iloc[-1]
    prev = d.iloc[-2]
    close = _safe_float(last.get("Close")) if '_safe_float' in globals() else float(last.get("Close"))
    high = _safe_float(last.get("High")) if '_safe_float' in globals() else float(last.get("High"))
    low = _safe_float(last.get("Low")) if '_safe_float' in globals() else float(last.get("Low"))
    prev_hh = _safe_float(prev.get("HH20")) if '_safe_float' in globals() else float(prev.get("HH20"))
    prev_ll = _safe_float(prev.get("LL10")) if '_safe_float' in globals() else float(prev.get("LL10"))
    hh_now = _safe_float(last.get("HH20")) if '_safe_float' in globals() else float(last.get("HH20"))
    ll_now = _safe_float(last.get("LL10")) if '_safe_float' in globals() else float(last.get("LL10"))
    if any(pd.isna(x) for x in [close, high, low, prev_hh, prev_ll, hh_now, ll_now]):
        return {"score": 0.0, "status": "HHLL 계산값 부족", "hh_breakout": False, "ll_breakdown": False}
    hh_breakout = high >= prev_hh or close > prev_hh
    ll_breakdown = low <= prev_ll or close < prev_ll
    width = max(prev_hh - prev_ll, 1e-9)
    hh_proximity = max(0.0, min(1.0, 1 - (prev_hh - close) / width))
    ll_risk = max(0.0, min(1.0, 1 - (close - prev_ll) / width))
    score = 0.0
    if hh_breakout:
        score += 2.0
    else:
        score += hh_proximity * 1.0
    if ll_breakdown:
        score -= 2.0
    else:
        score -= ll_risk * 0.8
    status = "HH20 돌파" if hh_breakout else "LL10 이탈" if ll_breakdown else "HH20 접근" if hh_proximity >= 0.75 else "중립"
    return {
        "score": round(float(score), 2),
        "status": status,
        "hh_breakout": bool(hh_breakout),
        "ll_breakdown": bool(ll_breakdown),
        "hh20": round(float(hh_now), 2),
        "ll10": round(float(ll_now), 2),
        "hh_proximity": round(float(hh_proximity), 2),
        "ll_risk": round(float(ll_risk), 2),
    }

# ---------------------------- News -----------------------------
def google_news_rss(query: str) -> List[Dict]:
    url = "https://news.google.com/rss/search?q=" + query.replace(" ", "+") + "&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries[:8]:
        items.append({"title": e.get("title", ""), "link": e.get("link", ""), "published": e.get("published", "")})
    return items

def kr_code_from_yf(ticker: str) -> str:
    base = str(ticker).upper().strip()
    return base.split(".")[0]

def get_kr_name(ticker: str) -> str:
    code = kr_code_from_yf(ticker).zfill(6)
    if krx_stock is not None and code.isdigit():
        try:
            name = krx_stock.get_market_ticker_name(code)
            if name and str(name).strip():
                return str(name).strip()
        except Exception:
            pass
    if code in KR_NAME_FALLBACK:
        return KR_NAME_FALLBACK[code]
    try:
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName")
        return name if name else ticker
    except Exception:
        return ticker

def collect_news(ticker: str) -> Dict:
    company = get_kr_name(ticker)
    code = kr_code_from_yf(ticker)
    return {
        "company": company,
        "us_economy_news": google_news_rss("한국 증시 코스피 코스닥 환율 금리 반도체 2차전지"),
        "company_news": google_news_rss(f"{company} {code} 실적 주가 공시"),
        "seeking_alpha_signal": google_news_rss(f"{company} {code} 증권사 리포트 목표주가 투자의견")
    }

# ---------------------- Rule-based agent -----------------------
def technical_score(df: pd.DataFrame) -> Dict:
    last = df.iloc[-1]
    score = 0
    reasons = []
    close = float(last["Close"])
    ma20, ma60, ma120 = float(last.get("MA20", np.nan)), float(last.get("MA60", np.nan)), float(last.get("MA120", np.nan))
    rsi14 = float(last.get("RSI14", np.nan))
    ret20 = float(last.get("RET20", 0))

    if close > ma20: score += 1; reasons.append("종가가 20일선 위")
    else: score -= 1; reasons.append("종가가 20일선 아래")
    if close > ma60: score += 1; reasons.append("중기 추세 양호")
    else: score -= 1; reasons.append("중기 추세 약함")
    if ma20 > ma60: score += 1; reasons.append("20일선이 60일선 위")
    else: score -= 1; reasons.append("20일선이 60일선 아래")
    if 45 <= rsi14 <= 65: score += 0.5; reasons.append(f"RSI {rsi14:.1f}: 과열 아님")
    elif rsi14 > 70: score -= 1; reasons.append(f"RSI {rsi14:.1f}: 단기 과열")
    elif rsi14 < 35: score -= 0.5; reasons.append(f"RSI {rsi14:.1f}: 약세/과매도")
    if ret20 > 0.05: score += 0.5; reasons.append("최근 20거래일 상승 모멘텀")
    elif ret20 < -0.05: score -= 0.5; reasons.append("최근 20거래일 하락 모멘텀")
    return {"score": score, "reasons": reasons, "close": close, "rsi": rsi14}

def keyword_sentiment(news: Dict) -> Dict:
    """국내 뉴스 헤드라인 점수. 목표가/실적/가이던스 키워드에 가중치를 더 줍니다."""
    text = " ".join([x.get("title", "") for k in news for x in (news[k] if isinstance(news[k], list) else [])]).lower()
    weighted_pos = {
        "목표가 상향": 1.8, "투자의견 상향": 1.8, "매수": 1.2, "어닝 서프라이즈": 2.0, "깜짝 실적": 1.8,
        "실적 개선": 1.4, "흑자전환": 1.8, "수주": 1.2, "신고가": 1.0, "성장": 0.8, "호실적": 1.4,
        "beat": 1.4, "upgrade": 1.5, "growth": 0.8, "strong": 0.7, "record": 1.0
    }
    weighted_neg = {
        "목표가 하향": 1.8, "투자의견 하향": 1.8, "매도": 1.2, "어닝 쇼크": 2.0, "실적 부진": 1.5,
        "적자전환": 1.8, "감익": 1.3, "하락": 0.7, "소송": 1.0, "리콜": 1.2, "리스크": 0.8,
        "miss": 1.4, "downgrade": 1.5, "weak": 0.8, "risk": 0.7, "loss": 0.8
    }
    p = sum(text.count(k.lower()) * w for k, w in weighted_pos.items())
    n = sum(text.count(k.lower()) * w for k, w in weighted_neg.items())
    return {"score": round((p - n) * 0.25, 2), "positive_hits": round(p, 2), "negative_hits": round(n, 2)}

def volume_score(df: pd.DataFrame) -> Dict:
    if df is None or df.empty or len(df) < 25:
        return {"score": 0.0, "reasons": ["거래량 데이터 부족"], "volume_ratio": 0.0}
    last = df.iloc[-1]
    score = 0.0
    reasons = []
    vr = _safe_float(last.get("VOL_RATIO", np.nan))
    obv = _safe_float(last.get("OBV", np.nan))
    obv_ma = _safe_float(last.get("OBV_MA20", np.nan))
    if not np.isnan(vr):
        if vr >= 2.0:
            score += 1.5; reasons.append(f"거래량이 20봉 평균의 {vr:.1f}배")
        elif vr >= 1.3:
            score += 0.8; reasons.append(f"거래량 증가 {vr:.1f}배")
        elif vr < 0.7:
            score -= 0.3; reasons.append("거래량 감소")
    if not np.isnan(obv) and not np.isnan(obv_ma):
        if obv > obv_ma:
            score += 0.8; reasons.append("OBV가 20봉 평균 위")
        else:
            score -= 0.4; reasons.append("OBV가 20봉 평균 아래")
    return {"score": round(score, 2), "reasons": reasons, "volume_ratio": round(float(vr), 2) if not np.isnan(vr) else 0.0}

def fundamental_score(ticker: str) -> Dict:
    """yfinance 기반 재무 대체 점수. 국내 종목은 값이 비어 있을 수 있어 실패 시 0점 처리합니다."""
    score = 0.0
    reasons = []
    data = {}
    try:
        info = yf.Ticker(ticker).info or {}
        revenue_growth = _safe_float(info.get("revenueGrowth", np.nan))
        earnings_growth = _safe_float(info.get("earningsGrowth", np.nan))
        profit_margin = _safe_float(info.get("profitMargins", np.nan))
        forward_pe = _safe_float(info.get("forwardPE", np.nan))
        data = {"revenueGrowth": revenue_growth, "earningsGrowth": earnings_growth, "profitMargins": profit_margin, "forwardPE": forward_pe}
        if not np.isnan(revenue_growth):
            if revenue_growth > 0.10: score += 1.0; reasons.append("매출 성장률 양호")
            elif revenue_growth < -0.05: score -= 0.8; reasons.append("매출 성장률 둔화")
        if not np.isnan(earnings_growth):
            if earnings_growth > 0.10: score += 1.0; reasons.append("EPS/이익 성장률 양호")
            elif earnings_growth < -0.10: score -= 1.0; reasons.append("EPS/이익 성장률 부진")
        if not np.isnan(profit_margin):
            if profit_margin > 0.08: score += 0.7; reasons.append("순이익률 양호")
            elif profit_margin < 0: score -= 0.8; reasons.append("순손실 구간")
        if not np.isnan(forward_pe):
            if 0 < forward_pe <= 18: score += 0.5; reasons.append("Forward P/E 부담 제한")
            elif forward_pe > 45: score -= 0.5; reasons.append("Forward P/E 부담")
    except Exception:
        reasons.append("재무 데이터 제한")
    return {"score": round(score, 2), "reasons": reasons[:4], "data": data}

def supply_score(ticker: str) -> Dict:
    """한국 시장용 수급 점수: 최근 5거래일 외국인/기관 순매수 대체 지표."""
    if krx_stock is None:
        return {"score": 0.0, "reasons": ["pykrx 미설치로 수급 점수 생략"], "foreign": 0, "institution": 0}
    code = kr_code_from_yf(ticker)
    try:
        end = _krx_date_for_list()
        start = (dt.datetime.strptime(end, "%Y%m%d") - dt.timedelta(days=14)).strftime("%Y%m%d")
        tv = krx_stock.get_market_trading_value_by_date(start, end, code)
        if tv is None or tv.empty:
            return {"score": 0.0, "reasons": ["수급 데이터 부족"], "foreign": 0, "institution": 0}
        foreign = 0.0
        institution = 0.0
        for col in tv.columns:
            if "외국인" in str(col):
                foreign += _safe_float(tv[col].tail(5).sum(), 0)
            if "기관" in str(col):
                institution += _safe_float(tv[col].tail(5).sum(), 0)
        score = 0.0
        reasons = []
        if foreign > 0: score += 0.8; reasons.append("최근 외국인 순매수")
        elif foreign < 0: score -= 0.5; reasons.append("최근 외국인 순매도")
        if institution > 0: score += 0.8; reasons.append("최근 기관 순매수")
        elif institution < 0: score -= 0.5; reasons.append("최근 기관 순매도")
        if foreign > 0 and institution > 0:
            score += 0.5; reasons.append("외국인·기관 동반 매수")
        return {"score": round(score, 2), "reasons": reasons, "foreign": int(foreign), "institution": int(institution)}
    except Exception as e:
        return {"score": 0.0, "reasons": ["수급 데이터 조회 실패"], "foreign": 0, "institution": 0}

def make_opinion(ticker: str, df: pd.DataFrame, news: Dict) -> Dict:
    tech = technical_score(df)
    sent = keyword_sentiment(news)
    vol = volume_score(df)
    fund = fundamental_score(ticker)
    supply = supply_score(ticker)
    turtle = {"score": 0.0, "status": "미반영"}
    try:
        hdf = fetch_hhll_price_preset(ticker, preset="1mo")
        turtle = turtle_metrics(hdf)
    except Exception:
        pass
    score = tech["score"] + sent["score"] + vol["score"] + fund["score"] + supply["score"] + float(turtle.get("score", 0))
    opinion = "매수" if score >= 3.0 else "매도" if score <= -2.0 else "관망"
    headlines = []
    for section in ["seeking_alpha_signal", "us_economy_news", "company_news"]:
        headlines += [x["title"] for x in news.get(section, [])[:3]]
    summary = (
        f"{opinion}\n"
        f"{ticker}의 기술점수는 {tech['score']:.1f}, 뉴스 키워드 점수는 {sent['score']:.1f}, 거래량 점수는 {vol['score']:.1f}, 수급 점수는 {supply['score']:.1f}, 재무 점수는 {fund['score']:.1f}, 터틀 점수는 {float(turtle.get('score', 0)):.1f}입니다.\n"
        f"핵심 기술 근거는 {', '.join(tech['reasons'][:4])}입니다.\n"
        f"거래량/수급 근거는 {', '.join((vol.get('reasons', []) + supply.get('reasons', []))[:4]) or '특이사항 없음'}입니다.\n"
        f"국내 증권 관련 헤드라인, 한국 경제뉴스, 기업뉴스를 함께 보면 현재 점수는 {score:.1f}로 평가됩니다.\n"
        f"최근 확인된 주요 헤드라인:"
    )
    return {"opinion": opinion, "summary": summary, "score": score, "tech": tech, "news": news, "volume": vol, "fundamental": fund, "supply": supply, "turtle": turtle}


# ------------------------- OpenAI option -----------------------
def llm_refine(ticker: str, raw: Dict) -> Dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return raw
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        prompt = f"""
너는 대한민국 주식 리서치 에이전트다. 반드시 첫 줄은 매수/매도/관망 중 하나로만 시작해라.
그 다음 줄부터 국내 증권/리포트 헤드라인, 한국 경제뉴스, 기업뉴스, 기술지표, 거래량, 외국인/기관 수급, 재무/실적, HHLL 터틀 점수를 종합해 한국어로 짧고 전문적으로 판단해라.
투자 조언이 아니라 정보 분석임을 마지막에 한 문장으로 넣어라.
데이터: {json.dumps(raw, ensure_ascii=False)[:12000]}
"""
        res = client.responses.create(model=os.getenv("OPENAI_MODEL", "gpt-5.5-mini"), input=prompt)
        text = res.output_text.strip()
        first = text.splitlines()[0].strip()
        op = "매수" if "매수" in first else "매도" if "매도" in first else "관망"
        raw.update({"opinion": op, "summary": text})
    except Exception as e:
        raw["summary"] += f"\n\nLLM 요약 실패: {e}"
    return raw

# -------------------------- Analysis ---------------------------
def today_key():
    return dt.datetime.now().strftime("%Y-%m-%d")

def save_analysis(ticker: str, result: Dict):
    ticker = resolve_kr_ticker(ticker)
    db_execute("""
    INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?,?)
    """, (ticker.upper(), today_key(), result["opinion"], result["summary"], float(result["score"]), json.dumps(result, ensure_ascii=False), dt.datetime.now().isoformat()))

def load_analysis(ticker: str, date: Optional[str] = None):
    ticker = resolve_kr_ticker(ticker)
    rows = db_execute("SELECT opinion, summary, score, payload, created_at FROM analyses WHERE ticker=? AND trade_date=?", (ticker.upper(), date or today_key()), True)
    return rows[0] if rows else None

def run_analysis(ticker: str, use_llm=True, force=False):
    ticker = resolve_kr_ticker(ticker)
    cached = load_analysis(ticker)
    if cached and not force:
        return json.loads(cached[3])
    df = fetch_price(ticker)
    if df.empty:
        raise ValueError(f"{ticker} 가격 데이터를 가져오지 못했습니다.")
    news = collect_news(ticker)
    result = make_opinion(ticker.upper(), df, news)
    if use_llm:
        result = llm_refine(ticker.upper(), result)
    save_analysis(ticker, result)
    return result


# ---------------------- 55 MA Reversal Scanner ----------------------
# 대한민국 증시 버전: KOSPI + KOSDAQ
# yfinance는 코스피 .KS, 코스닥 .KQ 접미사를 사용합니다.
FALLBACK_KOSPI = [
    "005930.KS","000660.KS","373220.KS","207940.KS","005380.KS","000270.KS","068270.KS","105560.KS","005490.KS","035420.KS",
    "012330.KS","055550.KS","028260.KS","035720.KS","066570.KS","032830.KS","086790.KS","003670.KS","015760.KS","009540.KS",
    "033780.KS","034020.KS","010130.KS","096770.KS","051910.KS","018260.KS","017670.KS","024110.KS","011200.KS","316140.KS"
]
FALLBACK_KOSDAQ = [
    "247540.KQ","086520.KQ","091990.KQ","028300.KQ","035900.KQ","263750.KQ","112040.KQ","214150.KQ","058470.KQ","041510.KQ",
    "145020.KQ","196170.KQ","068760.KQ","039030.KQ","293490.KQ","122870.KQ","067310.KQ","240810.KQ","178320.KQ","403870.KQ",
    "095340.KQ","000250.KQ","222800.KQ","357780.KQ","278280.KQ","036930.KQ","253450.KQ","131970.KQ","065350.KQ","121600.KQ"
]

def normalize_ticker(t: str) -> str:
    return str(t).strip().upper().replace(" ", "")

def _krx_date_for_list() -> str:
    # pykrx는 영업일 문자열 YYYYMMDD가 필요합니다. 주말/휴일이면 과거 며칠을 시도합니다.
    base = dt.datetime.now()
    for i in range(10):
        d = (base - dt.timedelta(days=i)).strftime("%Y%m%d")
        try:
            if krx_stock is not None and krx_stock.get_market_ticker_list(d, market="KOSPI"):
                return d
        except Exception:
            continue
    return base.strftime("%Y%m%d")

def get_krx_tickers(market: str) -> List[str]:
    if krx_stock is None:
        return FALLBACK_KOSPI if market == "KOSPI" else FALLBACK_KOSDAQ
    try:
        d = _krx_date_for_list()
        codes = krx_stock.get_market_ticker_list(d, market=market)
        suffix = ".KS" if market == "KOSPI" else ".KQ"
        return [f"{c}{suffix}" for c in codes if str(c).isdigit()]
    except Exception:
        return FALLBACK_KOSPI if market == "KOSPI" else FALLBACK_KOSDAQ

def get_scan_universe() -> List[str]:
    tickers = sorted(set(get_krx_tickers("KOSPI") + get_krx_tickers("KOSDAQ")))
    return [t for t in tickers if t and t not in {"N/A", "nan"}]

def resolve_kr_ticker(raw: str) -> str:
    t = normalize_ticker(raw)
    if not t:
        return "005930.KS"
    if t.endswith(".KS") or t.endswith(".KQ"):
        return t
    if t.isdigit():
        code = t.zfill(6)
        if krx_stock is not None:
            try:
                d = _krx_date_for_list()
                if code in krx_stock.get_market_ticker_list(d, market="KOSPI"):
                    return f"{code}.KS"
                if code in krx_stock.get_market_ticker_list(d, market="KOSDAQ"):
                    return f"{code}.KQ"
            except Exception:
                pass
        return f"{code}.KS"
    # 회사명 입력 시 pykrx로 최대한 찾아봅니다.
    if krx_stock is not None:
        try:
            d = _krx_date_for_list()
            for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ")]:
                for code in krx_stock.get_market_ticker_list(d, market=market):
                    name = krx_stock.get_market_ticker_name(code)
                    if t in name.upper().replace(" ", ""):
                        return f"{code}{suffix}"
        except Exception:
            pass
    return t

def _safe_float(x, default=np.nan) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _last_bar_by_previous_trading_day(d: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """30분봉 기준 직전 거래일 마지막 봉과 그 전 거래일 마지막 봉을 반환합니다."""
    if d is None or d.empty or len(d) < 3:
        raise ValueError("not enough data")
    tmp = d.copy()
    dates = pd.Series(tmp.index).dt.date.values if hasattr(tmp.index, "tz") or hasattr(tmp.index, "date") else pd.to_datetime(tmp.index).date
    tmp["_date"] = dates
    unique_dates = list(pd.Series(tmp["_date"]).drop_duplicates())
    if len(unique_dates) >= 3:
        yday_date = unique_dates[-2]
        before_date = unique_dates[-3]
        yday = tmp[tmp["_date"] == yday_date].iloc[-1]
        before = tmp[tmp["_date"] == before_date].iloc[-1]
        return yday, before
    return tmp.iloc[-2], tmp.iloc[-3]

def ema_reversal_metrics(df: pd.DataFrame) -> Optional[Dict]:
    # 스코어의 수렴/역전 점수는 30분봉 기준으로 계산합니다.
    # 8·13은 EMA, 21·55는 SMA입니다.
    if df is None or df.empty or len(df) < 70:
        return None
    d = enrich(df.dropna().copy())
    last = d.iloc[-1]
    prev5 = d.iloc[-6] if len(d) >= 6 else d.iloc[0]
    try:
        yday, before_yday = _last_bar_by_previous_trading_day(d)
    except Exception:
        yday = d.iloc[-2] if len(d) >= 2 else last
        before_yday = d.iloc[-3] if len(d) >= 3 else prev5

    close = _safe_float(last.get("Close"))
    ema8, ema13, ma21, ma55 = [_safe_float(last.get(x)) for x in ["EMA8","EMA13","MA21","MA55"]]
    old8, old13, old21, old55 = [_safe_float(prev5.get(x)) for x in ["EMA8","EMA13","MA21","MA55"]]
    y8, y13, y21, y55 = [_safe_float(yday.get(x)) for x in ["EMA8","EMA13","MA21","MA55"]]
    b8, b13, b21, b55 = [_safe_float(before_yday.get(x)) for x in ["EMA8","EMA13","MA21","MA55"]]

    values = [close, ema8, ema13, ma21, ma55, old8, old13, old21, old55, y8, y13, y21, y55, b8, b13, b21, b55]
    if any(np.isnan(x) for x in values):
        return None

    # 분석 실행 기준 직전 거래일에 EMA8/EMA13/MA21이 MA55를 아래에서 위로 돌파했는지 확인합니다.
    crossed_yesterday = {
        "EMA8": b8 <= b55 and y8 > y55,
        "EMA13": b13 <= b55 and y13 > y55,
        "MA21": b21 <= b55 and y21 > y55,
    }
    cross_count = sum(1 for v in crossed_yesterday.values() if v)
    full_yesterday_cross = cross_count == 3

    # 기존 후보 조건: 8/13/21선이 모두 55선 아래에 있고, 최근 5개 30분봉 동안 간격이 좁혀지는 종목
    below_now = ema8 < ma55 and ema13 < ma55 and ma21 < ma55
    gaps = np.array([(ma55-ema8)/ma55, (ma55-ema13)/ma55, (ma55-ma21)/ma55])
    old_gaps = np.array([(old55-old8)/old55, (old55-old13)/old55, (old55-old21)/old55])
    narrowing = old_gaps.mean() - gaps.mean()

    if not (full_yesterday_cross or (below_now and narrowing > 0)):
        return None

    signed_gaps = np.array([(ma55-ema8)/ma55, (ma55-ema13)/ma55, (ma55-ma21)/ma55])
    avg_gap_pct = float(np.abs(signed_gaps).mean() * 100)
    narrow_pct = float(max(0, narrowing) * 100)

    close_bonus = max(0, 5 - avg_gap_pct) * 1.2
    narrowing_bonus = min(4, max(0, narrow_pct * 2.5))
    slope_bonus = 0
    for now, old in [(ema8, old8), (ema13, old13), (ma21, old21)]:
        if now > old:
            slope_bonus += 0.4

    # 최고 가중치: 직전 거래일 30분봉 기준 세 선이 모두 55선을 상향 돌파
    yesterday_cross_bonus = 30.0 if full_yesterday_cross else cross_count * 6.0
    base_score = close_bonus + narrowing_bonus + slope_bonus + yesterday_cross_bonus

    if full_yesterday_cross:
        status = "직전 거래일 8EMA/13EMA/21MA 모두 55MA 상향 돌파"
    elif cross_count > 0:
        status = f"직전 거래일 {cross_count}개 선 부분 상향 돌파"
    else:
        status = "30분봉 기준 55MA 아래 수렴 중"

    return {
        "close": close,
        "ema8": ema8,
        "ema13": ema13,
        "ma21": ma21,
        "ma55": ma55,
        "avg_gap_pct": avg_gap_pct,
        "narrowing_pct": narrow_pct,
        "base_score": float(base_score),
        "yesterday_cross_bonus": float(yesterday_cross_bonus),
        "cross_count": int(cross_count),
        "status": status,
        "rsi": _safe_float(last.get("RSI14")),
        "volume": int(_safe_float(last.get("Volume"), 0)),
    }

def bulk_price_download(tickers: List[str], period="6mo", interval="1d") -> Dict[str, pd.DataFrame]:
    out = {}
    if not tickers:
        return out
    try:
        raw = yf.download(tickers, period=period, interval=interval, group_by="ticker", auto_adjust=False, progress=False, threads=True)
        if raw.empty:
            return out
        if isinstance(raw.columns, pd.MultiIndex):
            for t in tickers:
                if t in raw.columns.get_level_values(0):
                    sub = raw[t].dropna()
                    if not sub.empty:
                        out[t] = sub
        else:
            out[tickers[0]] = raw.dropna()
    except Exception:
        # yfinance 대량 다운로드가 실패하면 개별 다운로드로 일부라도 살립니다.
        for t in tickers:
            try:
                sub = yf.download(t, period=period, interval=interval, auto_adjust=False, progress=False)
                if not sub.empty:
                    if isinstance(sub.columns, pd.MultiIndex):
                        sub.columns = sub.columns.get_level_values(0)
                    out[t] = sub.dropna()
            except Exception:
                continue
    return out

def scan_ema_reversal_candidates(use_news=True, max_candidates=20) -> pd.DataFrame:
    universe = get_scan_universe()
    # 먼저 일봉 거래량으로 KOSPI + KOSDAQ 내 거래량 상위 100개를 고릅니다.
    daily_prices = bulk_price_download(universe, period="3mo", interval="1d")
    latest_vols = []
    for t, df in daily_prices.items():
        if df is not None and not df.empty and "Volume" in df.columns:
            latest_vols.append((t, _safe_float(df["Volume"].iloc[-1], 0)))
    top_volume = [t for t, _ in sorted(latest_vols, key=lambda x: x[1], reverse=True)[:100]]
    # 수렴/역전 점수는 30분봉 기준으로 다시 계산합니다.
    prices = bulk_price_download(top_volume, period="60d", interval="30m")

    rows = []
    for t in top_volume:
        metrics = ema_reversal_metrics(prices.get(t))
        if not metrics:
            continue
        try:
            company_name = get_kr_name(t)
        except Exception:
            company_name = t
        # 기술 점수는 기존 에이전트 규칙을 재활용합니다.
        try:
            tech = technical_score(enrich(prices[t]))
            tech_score = float(tech.get("score", 0))
        except Exception:
            tech_score = 0.0
        vol_score = 0.0
        fund_score = 0.0
        supply_score_v = 0.0
        try:
            vol_score = float(volume_score(enrich(prices[t])).get("score", 0))
        except Exception:
            pass
        try:
            fund_score = float(fundamental_score(t).get("score", 0))
        except Exception:
            pass
        try:
            supply_score_v = float(supply_score(t).get("score", 0))
        except Exception:
            pass
        news_score = 0.0
        headlines = []
        if use_news:
            try:
                news = collect_news(t)
                sent = keyword_sentiment(news)
                news_score = float(sent.get("score", 0))
                for section in ["seeking_alpha_signal", "us_economy_news", "company_news"]:
                    headlines.extend([x.get("title", "") for x in news.get(section, [])[:2] if x.get("title")])
            except Exception:
                pass
        turtle_score = 0.0
        turtle_status = ""
        try:
            hdf = fetch_hhll_price_preset(t, preset="1mo")
            tm = turtle_metrics(hdf)
            turtle_score = float(tm.get("score", 0))
            turtle_status = tm.get("status", "")
        except Exception:
            pass
        total_score = metrics["base_score"] + tech_score + news_score + turtle_score + vol_score + fund_score + supply_score_v
        rows.append({
            "ticker": t,
            "업체명": company_name,
            "score": round(total_score, 2),
            "수렴/역전점수": round(metrics["base_score"], 2),
            "터틀점수": round(turtle_score, 2),
            "터틀상태": turtle_status,
            "전일역전가점": round(metrics.get("yesterday_cross_bonus", 0), 2),
            "상태": metrics.get("status", ""),
            "기술점수": round(tech_score, 2),
            "뉴스점수": round(news_score, 2),
            "거래량점수": round(vol_score, 2),
            "수급점수": round(supply_score_v, 2),
            "재무점수": round(fund_score, 2),
            "55일선과 평균거리(%)": round(metrics["avg_gap_pct"], 2),
            "5일간 좁혀진 폭(%)": round(metrics["narrowing_pct"], 2),
            "종가": round(metrics["close"], 2),
            "EMA8": round(metrics["ema8"], 2),
            "EMA13": round(metrics["ema13"], 2),
            "MA21": round(metrics["ma21"], 2),
            "MA55": round(metrics["ma55"], 2),
            "거래량": int(metrics["volume"]),
            "주요 헤드라인": " | ".join(headlines[:3])
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("score", ascending=False).head(max_candidates).reset_index(drop=True)

# --------------------------- UI -------------------------------
def candle_chart(df: pd.DataFrame, ticker: str):
    fig = go.Figure()
    x = make_compact_x(df)
    fig.add_trace(go.Candlestick(x=x, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="Price"))
    for line_name in ["EMA8", "EMA13", "MA21", "MA55"]:
        if line_name in df.columns:
            fig.add_trace(go.Scatter(x=x, y=df[line_name], mode="lines", name=line_name))
    title_suffix = "분봉" if _is_intraday_index(df.index) else "일봉"
    fig.update_layout(title=f"{ticker} Chart ({title_suffix})", height=520, margin=dict(l=10,r=10,t=40,b=10), xaxis_rangeslider_visible=False)
    if _is_intraday_index(df.index):
        fig.update_xaxes(type="category", nticks=8)
    return fig

def explain_chart(df: pd.DataFrame):
    d = df.tail(180)
    fig = go.Figure()
    x = make_compact_x(d)
    fig.add_trace(go.Scatter(x=x, y=d["RSI14"], mode="lines", name="RSI(14)"))
    fig.add_hline(y=70, line_dash="dash")
    fig.add_hline(y=30, line_dash="dash")
    fig.update_layout(title="에이전트 설명용 그래프: RSI", height=260, margin=dict(l=10,r=10,t=40,b=10))
    if _is_intraday_index(d.index):
        fig.update_xaxes(type="category", nticks=8)
    return fig

def volume_chart(df: pd.DataFrame):
    d = df.tail(180).copy()
    fig = go.Figure()
    x = make_compact_x(d)
    fig.add_trace(go.Bar(x=x, y=d.get("Volume"), name="Volume"))
    if "VOL_MA20" in d.columns:
        fig.add_trace(go.Scatter(x=x, y=d["VOL_MA20"], mode="lines", name="Volume MA20"))
    fig.update_layout(title="거래량 참고 그래프: Volume + Volume MA20", height=260, margin=dict(l=10,r=10,t=40,b=10))
    if _is_intraday_index(d.index):
        fig.update_xaxes(type="category", nticks=8)
    return fig

def hhll_chart(df: pd.DataFrame, ticker: str, label: str):
    d = add_hhll(df).dropna().copy()
    fig = go.Figure()
    x = make_compact_x(d)
    fig.add_trace(go.Candlestick(x=x, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="15분봉 캔들"))
    fig.add_trace(go.Scatter(x=x, y=d["HH20"], mode="lines", name="Highest High 20"))
    fig.add_trace(go.Scatter(x=x, y=d["LL10"], mode="lines", name="Lowest Low 10"))
    # 돌파/이탈 신호 표시
    try:
        prev_hh = d["HH20"].shift(1)
        prev_ll = d["LL10"].shift(1)
        breakout = d[(d["High"] >= prev_hh) | (d["Close"] > prev_hh)]
        breakdown = d[(d["Low"] <= prev_ll) | (d["Close"] < prev_ll)]
        if not breakout.empty:
            bx = make_compact_x(breakout)
            fig.add_trace(go.Scatter(x=bx, y=breakout["High"], mode="markers", name="HH20 돌파", marker=dict(symbol="triangle-up", size=9)))
        if not breakdown.empty:
            sx = make_compact_x(breakdown)
            fig.add_trace(go.Scatter(x=sx, y=breakdown["Low"], mode="markers", name="LL10 이탈", marker=dict(symbol="triangle-down", size=9)))
    except Exception:
        pass
    tm = turtle_metrics(df)
    fig.update_layout(
        title=f"{ticker} HHLL 터틀트레이딩 참고 차트 · 15분봉 · {label} · 터틀점수 {tm.get('score', 0):.1f} / {tm.get('status', '중립')}",
        height=420,
        margin=dict(l=10,r=10,t=40,b=10),
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(type="category", nticks=8)
    return fig


def watchlist_rows():
    rows = db_execute("SELECT ticker,name,group_name,updated_at FROM watchlist", fetch=True) or []
    def sort_key(row):
        score = get_today_score(row[0])
        # 점수가 없는 종목은 아래로 보내고, 점수가 높은 종목부터 표시합니다.
        return (score is None, -(score if score is not None else -9999), row[2], row[0])
    return sorted(rows, key=sort_key)

def add_watch(ticker, group):
    yf_ticker = resolve_kr_ticker(ticker)
    name = get_kr_name(yf_ticker)
    db_execute("INSERT OR REPLACE INTO watchlist VALUES(?,?,?,?)", (yf_ticker.upper(), name, group, dt.datetime.now().isoformat()))

def remove_watch(ticker):
    db_execute("DELETE FROM watchlist WHERE ticker=?", (resolve_kr_ticker(ticker).upper(),))


def score_badge_html(score):
    if score is None:
        return "<span class='score-badge score-empty'>-</span>"
    try:
        score = float(score)
    except Exception:
        return "<span class='score-badge score-empty'>-</span>"
    cls = "score-buy" if score >= 1 else "score-sell" if score <= -1 else "score-hold"
    return f"<span class='score-badge {cls}'>{score:.1f}</span>"

def get_today_score(ticker):
    cached = load_analysis(ticker)
    if not cached:
        return None
    try:
        return float(cached[2])
    except Exception:
        return None

def headline_titles(result: Dict, limit: int = 5) -> List[str]:
    news = result.get("news", {}) or {}
    titles = []
    for section in ["seeking_alpha_signal", "us_economy_news", "company_news"]:
        titles.extend([item.get("title", "") for item in news.get(section, []) if item.get("title")])
    return titles[:limit]

def summary_lines(result: Dict, ticker: str) -> List[str]:
    tech = result.get("tech", {}) or {}
    sent = keyword_sentiment(result.get("news", {}) or {})
    vol = result.get("volume", {}) or {}
    fund = result.get("fundamental", {}) or {}
    supply = result.get("supply", {}) or {}
    turtle = result.get("turtle", {}) or {}
    tech_score = float(tech.get("score", 0))
    news_score = float(sent.get("score", 0))
    vol_score = float(vol.get("score", 0))
    fund_score = float(fund.get("score", 0))
    supply_score_v = float(supply.get("score", 0))
    turtle_score = float(turtle.get("score", 0))
    total_score = float(result.get("score", tech_score + news_score + vol_score + fund_score + supply_score_v + turtle_score))
    reasons = tech.get("reasons", []) or []
    reason_text = ", ".join(reasons[:4]) if reasons else "확인 가능한 기술 근거가 부족합니다"
    flow_text = ", ".join((vol.get("reasons", []) or []) + (supply.get("reasons", []) or [])) or "거래량/수급 특이사항 없음"
    fund_text = ", ".join(fund.get("reasons", []) or []) or "재무 데이터 제한 또는 중립"
    return [
        f"{ticker}의 기술점수는 {tech_score:.1f}, 뉴스 키워드 점수는 {news_score:.1f}, 거래량 점수는 {vol_score:.1f}, 수급 점수는 {supply_score_v:.1f}, 재무 점수는 {fund_score:.1f}, 터틀 점수는 {turtle_score:.1f}입니다.",
        f"핵심 기술 근거는 {reason_text}입니다.",
        f"거래량/수급 근거는 {flow_text}입니다.",
        f"재무/실적 근거는 {fund_text}입니다.",
        f"국내 증권 관련 헤드라인, 한국 경제뉴스, 기업뉴스를 함께 보면 현재 점수는 {total_score:.1f}로 평가됩니다.",
        "최근 확인된 주요 헤드라인:",
    ]



# ---------------------- V4 Market / Sector / Backtest ----------------------
KOREA_MARKET_INDEX = {"KOSPI":"^KS11", "KOSDAQ":"^KQ11"}
KOREA_SECTOR_ETFS = {
    "반도체":"091160.KS", "2차전지":"305540.KS", "바이오":"143860.KS", "제약·의료":"266420.KS", "로봇":"445290.KS", "자동차":"091180.KS",
    "은행":"091170.KS", "증권":"102970.KS", "화학":"091230.KS", "철강":"139240.KS", "IT":"139260.KS"
}


def trend_state_from_df(df: pd.DataFrame) -> Dict:
    if df is None or df.empty or len(df) < 60:
        return {"score":0.0,"state":"데이터 부족","reasons":["데이터 부족"]}
    d=enrich(df.copy()).dropna()
    if d.empty: return {"score":0.0,"state":"데이터 부족","reasons":["데이터 부족"]}
    last=d.iloc[-1]
    score=0.0; reasons=[]
    close=_safe_float(last.get('Close')); ma20=_safe_float(last.get('MA20')); ma60=_safe_float(last.get('MA60')); r=_safe_float(last.get('RSI14'))
    ret20=_safe_float(last.get('RET20'),0)*100
    if close>ma20: score+=1; reasons.append('20일선 위')
    else: score-=1; reasons.append('20일선 아래')
    if close>ma60: score+=1; reasons.append('60일선 위')
    else: score-=1; reasons.append('60일선 아래')
    if ma20>ma60: score+=1; reasons.append('20일선>60일선')
    else: score-=1; reasons.append('20일선<60일선')
    if ret20>3: score+=0.7; reasons.append(f'20일 수익률 +{ret20:.1f}%')
    elif ret20<-3: score-=0.7; reasons.append(f'20일 수익률 {ret20:.1f}%')
    if 45<=r<=65: score+=0.3; reasons.append(f'RSI {r:.1f} 안정')
    elif r>70: score-=0.3; reasons.append(f'RSI {r:.1f} 과열')
    state='강세' if score>=2 else '약세' if score<=-1 else '중립'
    return {"score":round(float(score),2),"state":state,"reasons":reasons,"close":round(float(close),2),"ret20":round(float(ret20),2)}


def market_dashboard() -> pd.DataFrame:
    rows=[]
    for name,ticker in KOREA_MARKET_INDEX.items():
        try:
            df=fetch_price(ticker, period='8mo', interval='1d')
            stt=trend_state_from_df(df)
            rows.append({"시장":name,"티커":ticker,"상태":stt['state'],"시장점수":stt['score'],"20일수익률%":stt.get('ret20',0),"근거":', '.join(stt.get('reasons',[])[:4])})
        except Exception as e:
            rows.append({"시장":name,"티커":ticker,"상태":"수집 실패","시장점수":0,"20일수익률%":0,"근거":str(e)[:80]})
    return pd.DataFrame(rows).sort_values('시장점수', ascending=False)


def sector_dashboard() -> pd.DataFrame:
    rows=[]
    for name,ticker in KOREA_SECTOR_ETFS.items():
        try:
            df=fetch_price(ticker, period='8mo', interval='1d')
            stt=trend_state_from_df(df)
            rows.append({"섹터":name,"ETF/대표지수":ticker,"상태":stt['state'],"섹터점수":stt['score'],"20일수익률%":stt.get('ret20',0),"근거":', '.join(stt.get('reasons',[])[:4])})
        except Exception as e:
            rows.append({"섹터":name,"ETF/대표지수":ticker,"상태":"수집 실패","섹터점수":0,"20일수익률%":0,"근거":str(e)[:80]})
    return pd.DataFrame(rows).sort_values(['섹터점수','20일수익률%'], ascending=False)


def trade_plan(df: pd.DataFrame) -> Dict:
    if df is None or df.empty or len(df)<30:
        return {"entry":None,"stop":None,"target":None,"risk_reward":None}
    d=add_hhll(enrich(df.copy())).dropna()
    last=d.iloc[-1]
    entry=_safe_float(last.get('Close'))
    stop_candidates=[_safe_float(last.get('LL10')), entry*0.94]
    stop=max([x for x in stop_candidates if not np.isnan(x) and x<entry], default=entry*0.94)
    risk=max(entry-stop, entry*0.02)
    target=entry+risk*2
    return {"entry":round(float(entry),0),"stop":round(float(stop),0),"target":round(float(target),0),"risk_reward":"1:2"}


def simple_backtest(tickers: List[str], max_names:int=30) -> pd.DataFrame:
    rows=[]
    for t in tickers[:max_names]:
        try:
            df=fetch_price(t, period='2y', interval='1d')
            if df.empty or len(df)<120: continue
            d=add_hhll(enrich(df.copy())).dropna().copy()
            trades=[]
            for i in range(60, len(d)-11):
                row=d.iloc[i]; prev=d.iloc[i-1]
                signal=(prev['EMA8']<=prev['MA55'] and row['EMA8']>row['MA55']) or (row['Close']>prev['HH20'])
                if signal:
                    entry=float(row['Close']); future=d.iloc[i+1:i+11]
                    if future.empty: continue
                    stop=float(row['LL10']) if not pd.isna(row['LL10']) else entry*0.94
                    target=entry+(entry-stop)*2
                    outcome=float(future['Close'].iloc[-1])
                    hit_target=(future['High']>=target).any(); hit_stop=(future['Low']<=stop).any()
                    if hit_target and not hit_stop: ret=(target/entry-1)
                    elif hit_stop and not hit_target: ret=(stop/entry-1)
                    else: ret=(outcome/entry-1)
                    trades.append(ret)
            if trades:
                win=sum(1 for x in trades if x>0)/len(trades)*100
                avg=np.mean(trades)*100
                rows.append({"종목":f"{t} · {get_kr_name(t)}","거래수":len(trades),"승률%":round(win,1),"평균수익%":round(avg,2),"총점":round(win/10+avg,2)})
        except Exception:
            continue
    return pd.DataFrame(rows).sort_values('총점', ascending=False) if rows else pd.DataFrame()


def market_weight() -> float:
    try:
        m=market_dashboard()
        avg=float(m['시장점수'].mean())
        return 1.15 if avg>=2 else 0.85 if avg<=-1 else 1.0
    except Exception:
        return 1.0


def ai_report_lines(ticker: str, result: Dict, df: pd.DataFrame) -> List[str]:
    plan=trade_plan(df)
    mw=market_weight()
    adjusted=float(result.get('score',0))*mw
    return [
        f"시장 가중치 적용 점수는 {adjusted:.1f}입니다. 현재 시장 가중치는 {mw:.2f}입니다.",
        f"참고 진입가는 {plan.get('entry')}, 손절가는 {plan.get('stop')}, 1차 목표가는 {plan.get('target')}입니다.",
        f"리스크 보상비는 {plan.get('risk_reward')} 기준으로 계산했습니다.",
        "이 가격대는 자동매매 신호가 아니라 국내 증시 차트 기반 참고 계획입니다."
    ]



# ---------------------- V10 Portfolio / Risk / Journal ----------------------
def init_v10_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_journal(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        side TEXT,
        entry REAL,
        stop REAL,
        target REAL,
        quantity REAL,
        status TEXT DEFAULT 'OPEN',
        exit_price REAL,
        note TEXT,
        opened_at TEXT,
        closed_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        capital REAL,
        risk_pct REAL,
        payload TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS recommendation_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        source TEXT,
        score REAL,
        entry REAL,
        recommended_at TEXT,
        horizon_days INTEGER DEFAULT 30,
        checked_at TEXT,
        current_price REAL,
        return_pct REAL,
        status TEXT DEFAULT 'OPEN',
        note TEXT
    )""")
    con.commit(); con.close()


def _now_text():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def max_drawdown(returns: List[float]) -> float:
    if not returns:
        return 0.0
    eq = np.cumprod([1 + r for r in returns])
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1) * 100
    return round(float(dd.min()), 2)


def enhanced_backtest(tickers: List[str], max_names:int=30) -> pd.DataFrame:
    """V10: 승률뿐 아니라 MDD, 손익비, 기대값까지 보는 간단 검증입니다."""
    rows=[]
    for t in tickers[:max_names]:
        try:
            df=fetch_price(t, period='5y', interval='1d')
            if df.empty or len(df)<180:
                continue
            d=add_hhll(enrich(df.copy())).dropna().copy()
            trades=[]
            for i in range(80, len(d)-16):
                row=d.iloc[i]; prev=d.iloc[i-1]
                signal = (prev['EMA8']<=prev['MA55'] and row['EMA8']>row['MA55']) or (row['Close']>prev['HH20'])
                if not signal:
                    continue
                entry=float(row['Close'])
                stop=float(row['LL10']) if not pd.isna(row['LL10']) and row['LL10'] < entry else entry*0.94
                target=entry+(entry-stop)*2
                future=d.iloc[i+1:i+16]
                if future.empty:
                    continue
                ret=None
                for _, f in future.iterrows():
                    if float(f['Low']) <= stop:
                        ret=stop/entry-1; break
                    if float(f['High']) >= target:
                        ret=target/entry-1; break
                if ret is None:
                    ret=float(future['Close'].iloc[-1])/entry-1
                trades.append(ret)
            if trades:
                wins=[x for x in trades if x>0]; losses=[x for x in trades if x<=0]
                win_rate=len(wins)/len(trades)*100
                avg=np.mean(trades)*100
                profit_factor=(sum(wins)/abs(sum(losses))) if losses and abs(sum(losses))>0 else np.nan
                mdd=max_drawdown(trades)
                expectancy=np.mean(trades)*100
                rows.append({"티커":t,"종목명":get_kr_name(t),"거래수":len(trades),"승률%":round(win_rate,1),"평균수익%":round(avg,2),"기대값%":round(expectancy,2),"Profit Factor":round(float(profit_factor),2) if not np.isnan(profit_factor) else None,"MDD%":mdd,"검증점수":round(win_rate/10+expectancy+(0 if np.isnan(profit_factor) else min(profit_factor,3)),2)})
        except Exception:
            continue
    return pd.DataFrame(rows).sort_values('검증점수', ascending=False) if rows else pd.DataFrame()


def position_size_from_plan(capital: float, risk_pct: float, entry: float, stop: float) -> Dict:
    if not entry or not stop or entry <= stop or capital <= 0:
        return {"risk_amount":0,"quantity":0,"position_value":0,"weight_pct":0}
    risk_amount = capital * risk_pct / 100
    unit_risk = entry - stop
    qty = max(0, int(risk_amount / unit_risk))
    position_value = qty * entry
    return {"risk_amount":round(risk_amount,2),"quantity":qty,"position_value":round(position_value,2),"weight_pct":round(position_value/capital*100,2) if capital else 0}


def build_portfolio_plan(source_df: pd.DataFrame, capital: float, max_positions:int=8, risk_pct:float=1.0) -> pd.DataFrame:
    if source_df is None or source_df.empty or capital <= 0:
        return pd.DataFrame()
    df = source_df.copy()
    score_col = 'score' if 'score' in df.columns else '총점' if '총점' in df.columns else '검증점수' if '검증점수' in df.columns else None
    ticker_col = 'ticker' if 'ticker' in df.columns else '티커' if '티커' in df.columns else None
    if not ticker_col:
        return pd.DataFrame()
    if score_col:
        df = df.sort_values(score_col, ascending=False)
    df = df.head(max_positions)
    rows=[]
    if score_col:
        total_score = max(float(df[score_col].fillna(0).clip(lower=0).sum()), 1.0)
    else:
        total_score = max(len(df), 1)
    for _, r in df.iterrows():
        t=str(r[ticker_col])
        try:
            price_df=fetch_price(t, period='6mo', interval='1d')
            plan=trade_plan(price_df)
            entry=plan.get('entry'); stop=plan.get('stop'); target=plan.get('target')
            ps=position_size_from_plan(capital, risk_pct, entry, stop)
            s=float(r[score_col]) if score_col and pd.notna(r[score_col]) else 1.0
            score_weight=round(max(s,0)/total_score*100,2)
            rows.append({"티커":t,"종목명":get_kr_name(t),"점수":round(s,2),"점수비중%":score_weight,"참고진입가":entry,"손절가":stop,"목표가":target,"권장수량":ps['quantity'],"포지션금액":ps['position_value'],"계좌비중%":ps['weight_pct'],"1회위험금액":ps['risk_amount']})
        except Exception:
            continue
    return pd.DataFrame(rows)


def save_portfolio_plan(df: pd.DataFrame, capital: float, risk_pct: float):
    if df is None or df.empty:
        return
    db_execute("INSERT INTO portfolio_plans(created_at, capital, risk_pct, payload) VALUES(?,?,?,?)", (_now_text(), float(capital), float(risk_pct), df.to_json(orient='records', force_ascii=False)))


def add_trade_journal(ticker, side, entry, stop, target, quantity, note=""):
    db_execute("""INSERT INTO trade_journal(ticker,side,entry,stop,target,quantity,status,note,opened_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (ticker, side, float(entry), float(stop), float(target), float(quantity), 'OPEN', note, _now_text()))


def close_trade_journal(row_id:int, exit_price:float):
    db_execute("UPDATE trade_journal SET status='CLOSED', exit_price=?, closed_at=? WHERE id=?", (float(exit_price), _now_text(), int(row_id)))


def journal_df() -> pd.DataFrame:
    rows=db_execute("SELECT id,ticker,side,entry,stop,target,quantity,status,exit_price,note,opened_at,closed_at FROM trade_journal ORDER BY id DESC", fetch=True)
    cols=['id','티커','방향','진입가','손절가','목표가','수량','상태','청산가','메모','진입일','청산일']
    df=pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df.insert(2, '종목명', df['티커'].map(get_kr_name))
        df['손익%']=df.apply(lambda r: round(((r['청산가'] if pd.notna(r['청산가']) else r['진입가'])/r['진입가']-1)*100,2) if r['진입가'] else 0, axis=1)
        df['손익금']=df.apply(lambda r: round((((r['청산가'] if pd.notna(r['청산가']) else r['진입가'])-r['진입가'])*r['수량']),2) if r['진입가'] else 0, axis=1)
    return df


def watchlist_source_df() -> pd.DataFrame:
    rows=watchlist_rows()
    out=[]
    for t,*_ in rows:
        sc=get_today_score(t)
        if sc is not None:
            out.append({'ticker':t,'score':float(sc)})
    return pd.DataFrame(out).sort_values('score', ascending=False) if out else pd.DataFrame()



# ---------------------- V6 Korea Regime / Rotation / Learning ----------------------
def classify_market_regime() -> Dict:
    """한국 증시 체제 엔진: KOSPI와 KOSDAQ 추세를 함께 봅니다."""
    try:
        kospi = trend_state_from_df(fetch_price('^KS11', period='1y', interval='1d'))
        kosdaq = trend_state_from_df(fetch_price('^KQ11', period='1y', interval='1d'))
        avg = float(np.mean([kospi.get('score',0), kosdaq.get('score',0)]))
        if avg <= -1.5:
            regime = 'Risk Off / 방어'
            weight = 0.65
            guide = '신규 매수는 줄이고, 수급이 확실한 종목만 봅니다.'
        elif avg >= 2.0:
            regime = 'Risk On / 추세 강세'
            weight = 1.20
            guide = '외국인·기관 수급과 HH20 돌파가 동반된 종목을 우선합니다.'
        elif avg >= 0.5:
            regime = '회복·선별장'
            weight = 1.05
            guide = '섹터 로테이션과 거래량이 살아나는 종목을 선별합니다.'
        elif avg <= -0.5:
            regime = '조정·경계'
            weight = 0.85
            guide = '포지션 크기를 낮추고 손절 기준을 보수적으로 둡니다.'
        else:
            regime = '중립·횡보'
            weight = 1.00
            guide = '개별 종목 모멘텀과 외국인·기관 수급 확인이 중요합니다.'
        return {'시장체제': regime, '시장가중치': round(weight,2), '평균시장점수': round(avg,2), '운용가이드': guide}
    except Exception as e:
        return {'시장체제':'판단 실패', '시장가중치':1.0, '평균시장점수':0.0, '운용가이드':str(e)[:80]}


def sector_rotation_dashboard() -> pd.DataFrame:
    """한국 섹터 로테이션: KOSPI 대비 20일/60일 상대강도와 추세를 순위화합니다. 바이오·제약의료·로봇 포함."""
    rows=[]
    try:
        base = fetch_price('^KS11', period='1y', interval='1d')
        base_ret20 = _safe_float(base['Close'].pct_change(20).iloc[-1], 0) if base is not None and not base.empty else 0
        base_ret60 = _safe_float(base['Close'].pct_change(60).iloc[-1], 0) if base is not None and not base.empty else 0
    except Exception:
        base_ret20=base_ret60=0
    for name, ticker in KOREA_SECTOR_ETFS.items():
        try:
            df=fetch_price(ticker, period='1y', interval='1d')
            if df.empty or len(df)<70:
                continue
            stt=trend_state_from_df(df)
            ret20=_safe_float(df['Close'].pct_change(20).iloc[-1],0)*100
            ret60=_safe_float(df['Close'].pct_change(60).iloc[-1],0)*100
            rel20=ret20 - base_ret20*100
            rel60=ret60 - base_ret60*100
            rotation_score=stt['score'] + rel20/5 + rel60/10
            phase = '주도' if rotation_score>=3 else '개선' if rotation_score>=1 else '약화' if rotation_score<=-1 else '중립'
            rows.append({'섹터':name,'ETF/대표지수':ticker,'로테이션단계':phase,'로테이션점수':round(float(rotation_score),2),'섹터점수':stt['score'],'20일상대강도%':round(rel20,2),'60일상대강도%':round(rel60,2),'근거':', '.join(stt.get('reasons',[])[:3])})
        except Exception:
            continue
    return pd.DataFrame(rows).sort_values(['로테이션점수','20일상대강도%'], ascending=False) if rows else pd.DataFrame()


def save_recommendations_from_df(df: pd.DataFrame, source: str='scanner', horizon_days:int=30):
    if df is None or df.empty:
        return
    ticker_col = 'ticker' if 'ticker' in df.columns else '티커' if '티커' in df.columns else '종목' if '종목' in df.columns else None
    score_col = 'score' if 'score' in df.columns else '검증점수' if '검증점수' in df.columns else '총점' if '총점' in df.columns else None
    if not ticker_col:
        return
    today=dt.datetime.now().strftime('%Y-%m-%d')
    for _, r in df.head(50).iterrows():
        raw=str(r[ticker_col]).strip()
        t=raw.split(' · ')[0].strip().upper()
        exists=db_execute("SELECT id FROM recommendation_log WHERE ticker=? AND source=? AND substr(recommended_at,1,10)=?", (t, source, today), fetch=True)
        if exists:
            continue
        try:
            px=fetch_price(t, period='5d', interval='1d')
            entry=_safe_float(px['Close'].iloc[-1],0) if px is not None and not px.empty else 0
            score=float(r[score_col]) if score_col and pd.notna(r[score_col]) else 0.0
            db_execute("INSERT INTO recommendation_log(ticker,source,score,entry,recommended_at,horizon_days,note) VALUES(?,?,?,?,?,?,?)", (t, source, score, entry, _now_text(), int(horizon_days), '자동 추천 저장'))
        except Exception:
            continue


def recommendation_log_df() -> pd.DataFrame:
    rows=db_execute("SELECT id,ticker,source,score,entry,recommended_at,horizon_days,checked_at,current_price,return_pct,status,note FROM recommendation_log ORDER BY id DESC", fetch=True)
    cols=['id','종목','소스','추천점수','추천가','추천일','검증일수','확인일','현재가','수익률%','상태','메모']
    df=pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df.insert(2, '종목명', df['종목'].map(lambda x: get_kr_name(x)))
    return df


def verify_recommendations(force: bool=False) -> int:
    rows=db_execute("SELECT id,ticker,entry,recommended_at,horizon_days,status FROM recommendation_log WHERE status='OPEN'", fetch=True)
    updated=0
    now=dt.datetime.now()
    for row_id,ticker,entry,rec_at,horizon,status in rows:
        try:
            rec_dt=dt.datetime.strptime(rec_at[:10], '%Y-%m-%d')
            if (now-rec_dt).days < int(horizon) and not force:
                continue
            df=fetch_price(ticker, period='5d', interval='1d')
            if df is None or df.empty or not entry:
                continue
            current=_safe_float(df['Close'].iloc[-1],0)
            ret=(current/float(entry)-1)*100 if entry else 0
            db_execute("UPDATE recommendation_log SET checked_at=?, current_price=?, return_pct=?, status=? WHERE id=?", (_now_text(), current, round(ret,2), 'CHECKED', int(row_id)))
            updated+=1
        except Exception:
            continue
    return updated


def recommendation_performance_df() -> pd.DataFrame:
    df=recommendation_log_df()
    if df.empty:
        return pd.DataFrame()
    checked=df[df['상태']=='CHECKED'].copy()
    if checked.empty:
        return pd.DataFrame()
    checked['win']=checked['수익률%']>0
    rows=[]
    for src,g in checked.groupby('소스'):
        rows.append({'소스':src,'검증건수':len(g),'승률%':round(g['win'].mean()*100,1),'평균수익률%':round(g['수익률%'].mean(),2),'누적수익률%':round(g['수익률%'].sum(),2)})
    rows.append({'소스':'전체','검증건수':len(checked),'승률%':round(checked['win'].mean()*100,1),'평균수익률%':round(checked['수익률%'].mean(),2),'누적수익률%':round(checked['수익률%'].sum(),2)})
    return pd.DataFrame(rows).sort_values('평균수익률%', ascending=False)


def ai_investment_journal_text() -> List[str]:
    regime=classify_market_regime()
    perf=recommendation_performance_df()
    jdf=journal_df()
    lines=[f"현재 한국 증시 체제는 {regime.get('시장체제')}입니다. 시장 가중치는 {regime.get('시장가중치')}입니다.", regime.get('운용가이드','')]
    if isinstance(perf, pd.DataFrame) and not perf.empty:
        total=perf[perf['소스']=='전체'].iloc[0]
        lines.append(f"자동 추천 검증 결과는 총 {int(total['검증건수'])}건, 승률 {total['승률%']}%, 평균수익률 {total['평균수익률%']}%입니다.")
    if isinstance(jdf, pd.DataFrame) and not jdf.empty:
        open_n=len(jdf[jdf['상태']=='OPEN'])
        closed=jdf[jdf['상태']=='CLOSED']
        if not closed.empty:
            lines.append(f"매매일지 기준 청산 거래 승률은 {round((closed['손익%']>0).mean()*100,1)}%, 총손익은 {closed['손익금'].sum():,.0f}원입니다. 현재 미청산 포지션은 {open_n}개입니다.")
        else:
            lines.append(f"현재 미청산 포지션은 {open_n}개입니다. 아직 청산 기록이 부족합니다.")
    else:
        lines.append("매매일지가 비어 있습니다. 실제 매수·청산 기록을 쌓아야 조건의 성과를 검증할 수 있습니다.")
    return lines

init_db()
init_v10_db()
st.set_page_config(page_title="Korea Stock Agent Pro V6", page_icon="📈", layout="wide")
st.markdown("""
<style>
.stApp{font-size:80%;}
h1{font-size:2.0rem !important;}
h2{font-size:1.55rem !important;}
h3{font-size:1.25rem !important;}
[data-testid="stSidebar"] *{font-size:0.92rem;}
.score-badge{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;font-size:11px;font-weight:700;margin-left:6px;}
.score-buy{background:#dcfce7;color:#166534;border:1px solid #86efac;}
.score-sell{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;}
.score-hold{background:#fef9c3;color:#854d0e;border:1px solid #fde047;}
.score-empty{background:#e5e7eb;color:#6b7280;border:1px solid #d1d5db;}
.watch-row{display:flex;align-items:center;justify-content:space-between;margin:2px 0 8px 0;}
.watch-meta{font-size:12px;color:#6b7280;margin-top:-6px;margin-bottom:8px;}
</style>
""", unsafe_allow_html=True)
if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = "005930.KS"


# ---------------------- V7 Adaptive OS / Supply / Alerts ----------------------
def init_v12_db():
    """V12: 자동 가중치, 알림, 성과 학습을 위한 추가 테이블."""
    db_execute("""CREATE TABLE IF NOT EXISTS adaptive_weights(
        id INTEGER PRIMARY KEY CHECK(id=1),
        technical REAL DEFAULT 1.0,
        news REAL DEFAULT 1.0,
        volume REAL DEFAULT 1.0,
        turtle REAL DEFAULT 1.0,
        sector REAL DEFAULT 1.0,
        regime REAL DEFAULT 1.0,
        updated_at TEXT
    )""")
    db_execute("""INSERT OR IGNORE INTO adaptive_weights(id, technical, news, volume, turtle, sector, regime, updated_at)
                VALUES(1,1,1,1,1,1,1,?)""", (_now_text(),))
    db_execute("""CREATE TABLE IF NOT EXISTS alert_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        alert_type TEXT,
        message TEXT,
        score REAL,
        created_at TEXT
    )""")

def get_adaptive_weights() -> Dict:
    init_v12_db()
    rows = db_execute("SELECT technical,news,volume,turtle,sector,regime,updated_at FROM adaptive_weights WHERE id=1", fetch=True)
    if not rows:
        return {"technical":1,"news":1,"volume":1,"turtle":1,"sector":1,"regime":1,"updated_at":""}
    r = rows[0]
    return {"technical":r[0],"news":r[1],"volume":r[2],"turtle":r[3],"sector":r[4],"regime":r[5],"updated_at":r[6]}

def update_adaptive_weights() -> Dict:
    """추천 성과를 이용해 가중치를 보수적으로 자동 조정. 데이터가 적으면 기본값 유지."""
    init_v12_db()
    log = recommendation_log_df()
    if log is None or log.empty or '수익률%' not in log.columns:
        return get_adaptive_weights()
    checked = log.dropna(subset=['수익률%']).copy()
    if len(checked) < 5:
        return get_adaptive_weights()
    avg_ret = float(checked['수익률%'].tail(50).mean())
    win = float((checked['수익률%'].tail(50) > 0).mean())
    # 단순하지만 과최적화를 피하기 위해 0.75~1.35 범위로 제한
    boost = max(0.75, min(1.35, 1.0 + avg_ret/25.0 + (win-0.5)*0.45))
    # 최근 성과가 좋으면 추세·터틀·거래량 비중 확대, 나쁘면 방어적으로 낮춤
    technical = round(max(0.75, min(1.35, boost)), 2)
    volume = round(max(0.75, min(1.35, 1.0 + (boost-1)*0.9)), 2)
    turtle = round(max(0.75, min(1.35, 1.0 + (boost-1)*1.1)), 2)
    news = round(max(0.75, min(1.25, 1.0 + (boost-1)*0.55)), 2)
    sector = round(max(0.80, min(1.30, 1.0 + (boost-1)*0.75)), 2)
    regime = round(max(0.80, min(1.30, 1.0 + (boost-1)*0.85)), 2)
    db_execute("UPDATE adaptive_weights SET technical=?,news=?,volume=?,turtle=?,sector=?,regime=?,updated_at=? WHERE id=1",
               (technical, news, volume, turtle, sector, regime, _now_text()))
    return get_adaptive_weights()

def classify_market_regime() -> Dict:
    """V7: KOSPI/KOSDAQ 기반 시장 체제 엔진."""
    try:
        tickers = {'^KS11':'KOSPI','^KQ11':'KOSDAQ'}
        states=[]
        for t,n in tickers.items():
            d=fetch_price(t, period='1y', interval='1d')
            states.append(trend_state_from_df(d).get('score',0))
        avg=float(np.mean(states)) if states else 0
        kosdaq=fetch_price('^KQ11', period='3mo', interval='1d')
        ret20=float(kosdaq['Close'].pct_change(20).iloc[-1]*100) if kosdaq is not None and len(kosdaq)>25 else 0
        if avg <= -2.0:
            regime='Panic / 위험회피 극대화'; weight=0.50; cash=55
        elif avg < -0.75:
            regime='Risk Off / 약세·방어'; weight=0.70; cash=40
        elif ret20 > 8 and avg >= 2.0:
            regime='Euphoria / 과열 강세'; weight=0.95; cash=25
        elif avg >= 1.4:
            regime='Risk On / 추세 강세'; weight=1.20; cash=10
        elif ret20 > 3 and avg > 0:
            regime='Recovery / 회복 초입'; weight=1.10; cash=20
        else:
            regime='Neutral / 관망'; weight=0.90; cash=30
        return {'시장체제':regime,'시장가중치':round(weight,2),'평균시장점수':round(avg,2),'VIX':0,'권장현금비중%':cash,
                '운용가이드':f'{regime}: 후보 점수에 시장가중치 {weight:.2f}를 적용하고 현금 {cash}%를 기준으로 포트폴리오 위험을 조절합니다.'}
    except Exception as e:
        return {'시장체제':'데이터 부족','시장가중치':0.9,'평균시장점수':0,'VIX':0,'권장현금비중%':30,'운용가이드':str(e)}

def kr_flow_score(ticker: str) -> Dict:
    try:
        d=fetch_price(ticker, period='3mo', interval='1d')
        if d is None or d.empty:
            return {'score':0.0,'call_put_ratio':None,'comment':'수급 대체 데이터 부족'}
        vr=_safe_float(d['VOL_RATIO'].iloc[-1],1) if 'VOL_RATIO' in d.columns else 1
        obv_up = bool(d['OBV'].iloc[-1] > d['OBV_MA10'].iloc[-1]) if 'OBV' in d.columns and 'OBV_MA10' in d.columns else False
        score = min(1.5, max(-1.0, (vr-1)*0.45 + (0.5 if obv_up else -0.2)))
        return {'score':round(score,2),'call_put_ratio':None,'comment':f'국내판 대체 수급점수: 거래량배수 {vr:.2f}, OBV상승 {obv_up}'}
    except Exception:
        return {'score':0.0,'call_put_ratio':None,'comment':'수급 대체 데이터 수집 실패'}

def apply_adaptive_weights(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    w=get_adaptive_weights()
    d=df.copy()
    # 존재하는 점수 컬럼만 재가중. 원점수도 보존.
    d['raw_score']=d['score'] if 'score' in d.columns else (d['총점'] if '총점' in d.columns else (d['검증점수'] if '검증점수' in d.columns else 0))
    score=pd.Series(0.0,index=d.index)
    mapping=[
        ('technical_score','technical'),('기술점수','technical'),('SLA기술점수','technical'),('기술','technical'),
        ('news_score','news'),('뉴스점수','news'),('뉴스 키워드 점수','news'),('뉴스','news'),
        ('volume_score','volume'),('거래량점수','volume'),('수급점수','volume'),('수급','volume'),
        ('turtle_score','turtle'),('터틀점수','turtle'),('HHLL점수','turtle'),
        ('sector_score','sector'),('섹터점수','sector'),('로테이션점수','sector'),
        ('시장점수','regime'),('시장가중치','regime')
    ]
    used=False
    for col,key in mapping:
        if col in d.columns:
            score += pd.to_numeric(d[col], errors='coerce').fillna(0) * float(w.get(key,1))
            used=True
    # 기존 score만 있는 경우는 시장체제만 반영
    total_col = 'score' if 'score' in d.columns else ('총점' if '총점' in d.columns else ('검증점수' if '검증점수' in d.columns else None))
    if not used and total_col:
        score=pd.to_numeric(d[total_col], errors='coerce').fillna(0) * float(w.get('regime',1))
    elif total_col:
        # 수렴/역전 등 기존 통합점수의 정보 손실 방지를 위해 일부 반영
        score += pd.to_numeric(d[total_col], errors='coerce').fillna(0) * 0.35
    regime=classify_market_regime()
    score = score * float(regime.get('시장가중치',1))
    d['v12_score']=score.round(2)
    d=d.sort_values('v12_score', ascending=False)
    return d

def optimize_portfolio_v12(source_df: pd.DataFrame, capital: float, max_positions:int=8, risk_pct:float=1.0) -> pd.DataFrame:
    """점수 기반 + 시장체제 현금비중을 적용한 간단 포트폴리오 최적화."""
    if source_df is None or source_df.empty:
        return pd.DataFrame()
    d=apply_adaptive_weights(source_df).head(max_positions).copy()
    if d.empty:
        return d
    regime=classify_market_regime()
    invest_capital=capital * (1 - float(regime.get('권장현금비중%',30))/100)
    s=pd.to_numeric(d.get('v12_score', d.get('score', 1)), errors='coerce').fillna(1).clip(lower=0.1)
    weights=s/s.sum()
    rows=[]
    for idx,row in d.iterrows():
        t=str(row.get('ticker') or row.get('티커') or row.get('종목') or row.get('ticker',''))
        try:
            px=fetch_price(t, period='3mo', interval='1d')['Close'].iloc[-1]
        except Exception:
            px=np.nan
        alloc=float(invest_capital*weights.loc[idx])
        qty=int(alloc/px) if px and not pd.isna(px) and px>0 else 0
        rows.append({'티커':t,'종목명':get_kr_name(t),'v12_score':float(s.loc[idx]),'비중%':round(float(weights.loc[idx])*100,2),'배정금액':round(alloc,2),'현재가':round(float(px),2) if px==px else None,'권장수량':qty,'시장체제':regime.get('시장체제')})
    return pd.DataFrame(rows)

def detect_alerts_v12(tickers: List[str]) -> pd.DataFrame:
    alerts=[]
    for t in tickers[:80]:
        try:
            d=fetch_hhll_price_preset(t, '1d')
            tm=turtle_metrics(d)
            daily=fetch_price(t, period='6mo', interval='1d')
            ts=technical_score(daily).get('score',0) if daily is not None and not daily.empty else 0
            if tm.get('hh_breakout') or ts>=3:
                msg = 'HH20 돌파' if tm.get('hh_breakout') else '기술점수 강세'
                score=float(tm.get('score',0))+float(ts)
                alerts.append({'티커':t,'종목명':get_kr_name(t),'알림':msg,'점수':round(score,2),'시간':_now_text()})
                db_execute("INSERT INTO alert_log(ticker,alert_type,message,score,created_at) VALUES(?,?,?,?,?)", (t,msg,msg,score,_now_text()))
        except Exception:
            continue
    return pd.DataFrame(alerts).sort_values('점수',ascending=False) if alerts else pd.DataFrame()

def alert_log_df() -> pd.DataFrame:
    rows=db_execute("SELECT ticker,alert_type,message,score,created_at FROM alert_log ORDER BY id DESC LIMIT 200", fetch=True)
    df = pd.DataFrame(rows, columns=['티커','알림유형','메시지','점수','시간']) if rows else pd.DataFrame()
    if not df.empty:
        df.insert(1, '종목명', df['티커'].map(get_kr_name))
    return df

st.title("📈 Korea Stock Agent Pro V7 — AI 자동학습 투자 OS")
st.caption("관심그룹은 매일 1회 자동 캐시 분석, 비관심 종목은 분석 버튼 클릭 시에만 분석됩니다. 국내 증권/경제/기업 뉴스는 RSS 검색 헤드라인 기반으로 참고하며, 거래량·외국인/기관 수급·재무/실적·터틀 점수를 함께 반영합니다.")

with st.sidebar:
    st.header("설정")
    use_llm = st.toggle("OpenAI로 의견 고도화", value=bool(os.getenv("OPENAI_API_KEY")))
    group = st.text_input("관심그룹 이름", "기본 관심그룹")
    add_t = st.text_input("관심 종목 추가", "005930 또는 005930.KS")
    if st.button("관심그룹에 추가"):
        resolved = resolve_kr_ticker(add_t)
        add_watch(resolved, group); st.success(f"{resolved} 추가")
    st.divider()
    rows = watchlist_rows()
    st.write("관심그룹")
    if "delete_selected" not in st.session_state:
        st.session_state.delete_selected = {}
    for t, name, g, upd in rows:
        score = get_today_score(t)
        col_chk, col_a, col_b = st.columns([0.35, 3.65, 1])
        st.session_state.delete_selected[t] = col_chk.checkbox("", value=st.session_state.delete_selected.get(t, False), key=f"chk_{g}_{t}", label_visibility="collapsed")
        if col_a.button(f"{g} · {t} · {name}", key=f"select_{g}_{t}", width="stretch"):
            st.session_state.selected_ticker = t
            st.rerun()
        col_b.markdown(score_badge_html(score), unsafe_allow_html=True)
    if rows:
        if st.button("선택 종목 삭제", type="secondary", width="stretch"):
            selected = [t for t, checked in st.session_state.delete_selected.items() if checked]
            for t in selected:
                remove_watch(t)
            st.session_state.delete_selected = {}
            st.rerun()
    st.divider()
    if st.button("관심그룹 Daily 업그레이드 실행"):
        for t, *_ in rows:
            with st.spinner(f"{t} 분석 중..."):
                run_analysis(t, use_llm=use_llm, force=True)
        st.success("업데이트 완료")

main_tab, scanner_tab, market_tab, backtest_tab, portfolio_tab, journal_tab, learning_tab, v12_tab = st.tabs(["종목 차트·에이전트", "55일선 역전 후보", "시장·섹터·로테이션", "백테스트", "포트폴리오", "매매일지", "성과학습", "V7 자동학습"])

with main_tab:
    left, right = st.columns([1.35, 0.9], gap="large")

    with left:
        st.subheader("종목 검색 차트")
        raw_ticker = st.text_input("종목 검색", value=st.session_state.selected_ticker, help="예: 005930, 005930.KS, 035720.KS, 247540.KQ")
        ticker = resolve_kr_ticker(raw_ticker)
        st.session_state.selected_ticker = ticker
        st.caption(f"선택 종목: {ticker} · {get_kr_name(ticker)}")
        period = st.selectbox("기간", ["6mo", "1mo", "1wk", "1d", "30m", "1y", "2y", "5y"], index=0)
        df = fetch_price_preset(ticker, preset=period)
        if not df.empty:
            st.plotly_chart(candle_chart(df, ticker), width="stretch")
            st.subheader("에이전트 설명용 그래프")
            st.plotly_chart(explain_chart(df), width="stretch")
            st.plotly_chart(volume_chart(df), width="stretch")

            st.subheader("HHLL 터틀트레이딩 참고 차트")
            hhll_period = st.selectbox("HHLL 기간", ["6mo", "1mo", "1wk", "1d"], index=1)
            hhll_df = fetch_hhll_price_preset(ticker, preset=hhll_period)
            if not hhll_df.empty and len(hhll_df) >= 25:
                st.plotly_chart(hhll_chart(hhll_df, ticker, hhll_period), width="stretch")
                if hhll_period == "6mo":
                    st.caption("15분봉 장기 데이터는 데이터 제공처 제한으로 실제 표시 범위가 최근 약 60일로 줄어들 수 있습니다.")
            else:
                st.warning("HHLL 15분봉 데이터를 충분히 가져오지 못했습니다.")
        else:
            st.error("차트 데이터를 가져오지 못했습니다.")

    with right:
        st.subheader("에이전트 매수/매도 의견")
        is_watch = ticker in [r[0] for r in rows]
        cached = load_analysis(ticker)
        if is_watch and not cached:
            with st.spinner("관심종목이라 오늘 분석을 자동 실행합니다..."):
                result = run_analysis(ticker, use_llm=use_llm)
        elif cached:
            result = json.loads(cached[3])
        else:
            result = None

        if st.button("🔎 지금 분석", type="primary"):
            with st.spinner("뉴스와 차트를 종합 분석 중..."):
                result = run_analysis(ticker, use_llm=use_llm, force=True)

        if result:
            op = result["opinion"]
            st.metric("의견", op, f"score {result['score']:.1f}")
            st.markdown("**분석 결과**")
            for line in summary_lines(result, ticker):
                st.write(f"• {line}")

            st.markdown("**V4 실전 리포트**")
            try:
                for line in ai_report_lines(ticker, result, df):
                    st.write(f"• {line}")
            except Exception:
                pass

            st.markdown("**수집한 뉴스 헤드라인**")
            news = result.get("news", {})
            for section, title in [("seeking_alpha_signal", "국내 증권/리포트 뉴스"), ("us_economy_news", "한국 경제뉴스"), ("company_news", "기업뉴스")]:
                st.markdown(f"_{title}_")
                for item in news.get(section, [])[:6]:
                    headline = item.get("title", "")
                    link = item.get("link", "")
                    if link:
                        st.markdown(f"- [{headline}]({link})")
                    else:
                        st.write(f"- {headline}")
        else:
            st.info("관심그룹이 아닌 종목은 ‘🔎 지금 분석’을 눌러야 분석됩니다.")

with scanner_tab:
    st.subheader("KOSPI·KOSDAQ 55일선 역전 후보 스캐너")
    st.caption("KOSPI와 KOSDAQ 종목을 합쳐 최근 거래량 상위 100개를 먼저 고른 뒤, EMA8·EMA13·MA21이 MA55 아래에서 수렴 중이거나, 분석 실행 직전 거래일에 MA55를 상향 돌파한 종목을 찾습니다. 수렴 점수는 30분봉 기준이며, 15분봉 HHLL 터틀 점수도 score에 반영됩니다.")
    col1, col2, col3 = st.columns([1, 1, 2])
    use_news_scan = col1.toggle("뉴스 점수 포함", value=True)
    max_result = col2.selectbox("추천 개수", [20, 50], index=0)
    run_scan = col3.button("📊 후보 종목 분석 실행", type="primary", width="stretch")

    if run_scan:
        with st.spinner("거래량 상위 100개 종목과 EMA 수렴도를 분석 중입니다. 뉴스 점수 포함 시 시간이 더 걸릴 수 있습니다..."):
            scan_df = scan_ema_reversal_candidates(use_news=use_news_scan, max_candidates=int(max_result))
            scan_df = apply_adaptive_weights(scan_df)
        st.session_state["scan_df"] = scan_df

    scan_df = st.session_state.get("scan_df")
    if isinstance(scan_df, pd.DataFrame) and not scan_df.empty:
        st.markdown("**추천 후보 내림차순**")
        st.dataframe(scan_df, width="stretch", hide_index=True)
        st.caption("score = 30분봉 수렴/역전점수 + 기술점수 + 뉴스점수 + 거래량점수 + 수급점수 + 재무점수 + 터틀점수. 분석 실행 직전 거래일에 EMA8·EMA13·MA21이 모두 MA55를 상향 돌파하면 가장 높은 가중치를 줍니다.")
        st.markdown("**후보를 차트에서 열기**")
        cols = st.columns(5)
        scan_rows = scan_df[["ticker", "업체명"]].head(int(max_result)).to_dict("records") if "업체명" in scan_df.columns else [{"ticker": t, "업체명": ""} for t in scan_df["ticker"].tolist()[:int(max_result)]]
        for i, row in enumerate(scan_rows):
            t = row["ticker"]
            label = f"{t} · {row.get('업체명', '')}" if row.get("업체명") else t
            if cols[i % 5].button(label, key=f"open_scan_{t}"):
                st.session_state.selected_ticker = t
                st.rerun()
    else:
        st.info("‘후보 종목 분석 실행’을 누르면 결과가 표시됩니다.")


with market_tab:
    st.subheader("시장 상태 엔진")
    st.caption("KOSPI·KOSDAQ의 추세를 먼저 확인합니다. 강세장에서는 돌파 신호 신뢰도가 높고, 약세장에서는 후보 점수를 보수적으로 해석합니다.")
    if st.button("시장·섹터 업데이트", type="primary"):
        st.session_state['k_market_df']=market_dashboard()
        st.session_state['k_sector_df']=sector_dashboard()
    mdf=st.session_state.get('k_market_df')
    sdf=st.session_state.get('k_sector_df')
    if isinstance(mdf, pd.DataFrame):
        st.markdown("**시장 상태**")
        st.dataframe(mdf, width="stretch", hide_index=True)
    if isinstance(sdf, pd.DataFrame):
        st.markdown("**섹터 강도 순위**")
        st.dataframe(sdf, width="stretch", hide_index=True)
    st.markdown("**시장 체제 엔진**")
    regime = classify_market_regime()
    c1,c2,c3 = st.columns(3)
    c1.metric("시장 체제", regime.get('시장체제'))
    c2.metric("시장 가중치", regime.get('시장가중치'))
    c3.metric("평균 시장점수", regime.get('평균시장점수'))
    st.caption(regime.get('운용가이드'))
    if st.button("섹터 로테이션 분석", type="secondary"):
        st.session_state['k_rotation_df'] = sector_rotation_dashboard()
    rdf = st.session_state.get('k_rotation_df')
    if isinstance(rdf, pd.DataFrame) and not rdf.empty:
        st.markdown("**섹터 로테이션 순위 — 바이오·제약의료·로봇 포함**")
        st.dataframe(rdf, width="stretch", hide_index=True)
    if not isinstance(mdf, pd.DataFrame):
        st.info("버튼을 누르면 시장과 섹터 강도를 계산합니다.")

with backtest_tab:
    st.subheader("간단 백테스트")
    st.caption("최근 2년 일봉에서 EMA8의 MA55 상향 돌파 또는 HH20 돌파 후 10거래일 성과를 단순 검증합니다.")
    bt_count=st.selectbox("검증 종목 수", [20,30,50], index=1)
    if st.button("백테스트 실행", type="primary"):
        universe=get_scan_universe()
        daily=bulk_price_download(universe, period="3mo", interval="1d")
        vols=[]
        for t,dd in daily.items():
            if dd is not None and not dd.empty and 'Volume' in dd.columns:
                vols.append((t,_safe_float(dd['Volume'].iloc[-1],0)))
        top=[t for t,_ in sorted(vols,key=lambda x:x[1], reverse=True)[:int(bt_count)]] or universe[:int(bt_count)]
        with st.spinner("백테스트 계산 중..."):
            st.session_state['k_bt_df']=enhanced_backtest(top, max_names=int(bt_count))
    bdf=st.session_state.get('k_bt_df')
    if isinstance(bdf, pd.DataFrame) and not bdf.empty:
        st.dataframe(bdf, width="stretch", hide_index=True)
    else:
        st.info("백테스트 실행 버튼을 누르면 결과가 표시됩니다.")


with portfolio_tab:
    st.subheader("V10 포트폴리오·위험관리")
    st.caption("추천 종목을 그대로 매수하는 기능이 아니라, 계좌 규모와 1회 위험률 기준으로 참고 수량을 계산합니다.")
    c1,c2,c3,c4=st.columns([1,1,1,1])
    capital=c1.number_input("계좌 규모", min_value=1000.0, value=100000.0, step=1000.0)
    risk_pct=c2.number_input("1종목 위험률%", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
    max_pos=c3.selectbox("최대 종목 수", [5,8,10,15], index=1)
    src=c4.selectbox("포트폴리오 소스", ["후보 스캐너", "관심종목", "백테스트 상위"])
    if st.button("포트폴리오 제안 생성", type="primary"):
        if src=="후보 스캐너":
            source_df=st.session_state.get('scan_df')
        elif src=="백테스트 상위":
            source_df=st.session_state.get('bt_df') if 'bt_df' in st.session_state else st.session_state.get('k_bt_df')
        else:
            source_df=watchlist_source_df()
        if not isinstance(source_df, pd.DataFrame) or source_df.empty:
            st.warning("먼저 후보 스캐너, 백테스트, 또는 관심종목 Daily 업데이트를 실행하세요.")
        else:
            plan_df=build_portfolio_plan(source_df, capital, int(max_pos), float(risk_pct))
            st.session_state['portfolio_plan_df']=plan_df
            save_portfolio_plan(plan_df, capital, risk_pct)
    pdf=st.session_state.get('portfolio_plan_df')
    if isinstance(pdf, pd.DataFrame) and not pdf.empty:
        st.dataframe(pdf, width="stretch", hide_index=True)
        st.caption("권장수량은 손절가까지 갔을 때 계좌의 지정 위험률만 손실 보도록 계산한 참고값입니다.")
    else:
        st.info("포트폴리오 제안 생성 버튼을 누르면 결과가 표시됩니다.")

with journal_tab:
    st.subheader("V10 매매일지")
    st.caption("추천 → 실제 매수 → 결과를 기록해야 앱의 조건이 실제로 유효한지 알 수 있습니다.")
    with st.form("journal_add"):
        c1,c2,c3,c4,c5,c6=st.columns(6)
        default_t = st.session_state.get('selected_ticker','TSLA')
        jt=c1.text_input("티커", value=default_t).upper().strip()
        side=c2.selectbox("방향", ["BUY", "SELL"])
        entry=c3.number_input("진입가", min_value=0.0, value=0.0, step=0.01)
        stop=c4.number_input("손절가", min_value=0.0, value=0.0, step=0.01)
        target=c5.number_input("목표가", min_value=0.0, value=0.0, step=0.01)
        qty=c6.number_input("수량", min_value=0.0, value=0.0, step=1.0)
        note=st.text_input("메모", value="")
        submitted=st.form_submit_button("매매 기록 추가")
        if submitted and jt and entry>0 and qty>0:
            add_trade_journal(jt, side, entry, stop, target, qty, note)
            st.success("매매일지에 기록했습니다.")
    jdf=journal_df()
    if not jdf.empty:
        st.dataframe(jdf, width="stretch", hide_index=True)
        open_ids=jdf[jdf['상태']=='OPEN']['id'].tolist()
        if open_ids:
            c1,c2=st.columns([1,1])
            close_id=c1.selectbox("청산할 기록", open_ids)
            exit_price=c2.number_input("청산가", min_value=0.0, value=0.0, step=0.01)
            if st.button("선택 기록 청산") and exit_price>0:
                close_trade_journal(int(close_id), float(exit_price))
                st.rerun()
        closed=jdf[jdf['상태']=='CLOSED']
        if not closed.empty:
            win=(closed['손익%']>0).mean()*100
            total=closed['손익금'].sum()
            st.metric("기록된 매매 승률", f"{win:.1f}%", f"총손익 {total:,.0f}")
    else:
        st.info("아직 기록된 매매가 없습니다.")


with learning_tab:
    st.subheader("V6 성과학습·추천 검증")
    st.caption("추천 종목을 자동 저장하고, 지정 기간 뒤 실제 수익률을 확인합니다.")
    c1,c2,c3 = st.columns([1,1,2])
    if c1.button("30일 지난 추천 검증", type="primary"):
        n=verify_recommendations(force=False)
        st.success(f"검증 완료: {n}건 업데이트")
    if c2.button("전체 강제 재검증"):
        n=verify_recommendations(force=True)
        st.success(f"검증 완료: {n}건 업데이트")
    if c3.button("현재 후보를 추천 기록에 저장"):
        save_recommendations_from_df(st.session_state.get('k_scan_df'), source='scanner_manual', horizon_days=30)
        st.success("현재 후보를 추천 기록에 저장했습니다.")
    perf=recommendation_performance_df()
    if isinstance(perf, pd.DataFrame) and not perf.empty:
        st.markdown("**추천 성과 대시보드**")
        st.dataframe(perf, width="stretch", hide_index=True)
    else:
        st.info("아직 검증 완료된 추천 기록이 없습니다.")
    log=recommendation_log_df()
    if isinstance(log, pd.DataFrame) and not log.empty:
        st.markdown("**추천 기록**")
        st.dataframe(log, width="stretch", hide_index=True)
    st.markdown("**AI 투자일지 요약**")
    for line in ai_investment_journal_text():
        st.write(f"• {line}")


with v12_tab:
    st.subheader("V7 자동학습·수급흐름·최적화·알림")
    st.caption("추천 성과를 이용해 점수 가중치를 보수적으로 자동 조정하고, 국내 수급 대체 흐름·시장체제·알림을 함께 확인합니다.")
    c1,c2,c3 = st.columns(3)
    if c1.button("AI 가중치 자동 조정", type="primary"):
        st.session_state['v12_weights'] = update_adaptive_weights()
    if c2.button("현재 후보 V7 재점수화"):
        st.session_state['v12_rescore'] = apply_adaptive_weights(st.session_state.get('scan_df'))
    if c3.button("관심종목 알림 스캔"):
        st.session_state['v12_alerts'] = detect_alerts_v12([r[0] for r in watchlist_rows()])

    st.markdown("**자동학습 가중치**")
    st.json(st.session_state.get('v12_weights', get_adaptive_weights()))

    rdf = st.session_state.get('v12_rescore')
    if isinstance(rdf, pd.DataFrame) and not rdf.empty:
        st.markdown("**V7 재점수화 후보**")
        st.dataframe(rdf, width="stretch", hide_index=True)

    st.markdown("**국내 수급 대체 흐름 참고**")
    opt_ticker = st.text_input("국내 수급 대체 흐름 확인 티커", value=st.session_state.get('selected_ticker','TSLA')).upper().strip()
    if st.button("국내 수급 대체 흐름 확인"):
        st.json(kr_flow_score(opt_ticker))

    st.markdown("**V7 최적화 포트폴리오**")
    c1,c2,c3=st.columns(3)
    v12_cap=c1.number_input("V7 계좌 규모", min_value=1000.0, value=100000.0, step=1000.0)
    v12_pos=c2.selectbox("V7 최대 종목 수", [5,8,10,15], index=1)
    v12_risk=c3.number_input("V7 1종목 위험률%", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
    if st.button("V7 포트폴리오 최적화"):
        source = st.session_state.get('v12_rescore') if isinstance(st.session_state.get('v12_rescore'), pd.DataFrame) else st.session_state.get('scan_df')
        st.session_state['v12_portfolio'] = optimize_portfolio_v12(source, float(v12_cap), int(v12_pos), float(v12_risk))
    v12p=st.session_state.get('v12_portfolio')
    if isinstance(v12p, pd.DataFrame) and not v12p.empty:
        st.dataframe(v12p, width="stretch", hide_index=True)

    alerts=st.session_state.get('v12_alerts')
    if isinstance(alerts, pd.DataFrame) and not alerts.empty:
        st.markdown("**이번 스캔 알림**")
        st.dataframe(alerts, width="stretch", hide_index=True)
    hist=alert_log_df()
    if isinstance(hist, pd.DataFrame) and not hist.empty:
        st.markdown("**최근 알림 기록**")
        st.dataframe(hist, width="stretch", hide_index=True)
