import os, json, sqlite3, textwrap, datetime as dt
from dataclasses import dataclass
from typing import List, Dict, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
import feedparser
from dotenv import load_dotenv

load_dotenv()
DB_PATH = "stock_agent.db"
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
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA120"] = df["Close"].rolling(120).mean()
    df["RSI14"] = rsi(df["Close"], 14)
    df["RET20"] = df["Close"].pct_change(20)
    df["VOL20"] = df["Close"].pct_change().rolling(20).std() * np.sqrt(252)
    return df

def fetch_price(ticker: str, period="1y") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval="1d", auto_adjust=False, progress=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    return enrich(df)

# ---------------------------- News -----------------------------
def google_news_rss(query: str) -> List[Dict]:
    url = "https://news.google.com/rss/search?q=" + query.replace(" ", "+") + "&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries[:8]:
        items.append({"title": e.get("title", ""), "link": e.get("link", ""), "published": e.get("published", "")})
    return items

def collect_news(ticker: str) -> Dict:
    company = ticker
    try:
        info = yf.Ticker(ticker).info
        company = info.get("longName") or info.get("shortName") or ticker
    except Exception:
        pass
    return {
        "company": company,
        "us_economy_news": google_news_rss("US economy market interest rates inflation stocks"),
        "company_news": google_news_rss(f"{company} {ticker} stock earnings"),
        "seeking_alpha_signal": google_news_rss(f"site:seekingalpha.com {ticker} {company} stock analysis")
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
    text = " ".join([x["title"] for k in news for x in (news[k] if isinstance(news[k], list) else [])]).lower()
    pos = ["beat", "growth", "surge", "record", "upgrade", "profit", "margin", "strong", "buy", "bullish"]
    neg = ["miss", "cut", "weak", "lawsuit", "probe", "downgrade", "loss", "slow", "risk", "bearish", "tariff"]
    p = sum(text.count(w) for w in pos); n = sum(text.count(w) for w in neg)
    return {"score": (p - n) * 0.25, "positive_hits": p, "negative_hits": n}

def make_opinion(ticker: str, df: pd.DataFrame, news: Dict) -> Dict:
    tech = technical_score(df)
    sent = keyword_sentiment(news)
    score = tech["score"] + sent["score"]
    opinion = "매수" if score >= 1.0 else "매도" if score <= -1.0 else "관망"
    headlines = []
    for section in ["seeking_alpha_signal", "us_economy_news", "company_news"]:
        headlines += [x["title"] for x in news.get(section, [])[:3]]
    summary = (
        f"{opinion}\n"
        f"{ticker}의 기술점수는 {tech['score']:.1f}, 뉴스 키워드 점수는 {sent['score']:.1f}입니다.\n"
        f"핵심 기술 근거는 {', '.join(tech['reasons'][:4])}입니다.\n"
        f"시킹알파 관련 헤드라인, 미국 경제뉴스, 기업뉴스를 함께 보면 현재 점수는 {score:.1f}로 평가됩니다.\n"
        f"최근 확인된 주요 헤드라인:"
    )
    return {"opinion": opinion, "summary": summary, "score": score, "tech": tech, "news": news}

# ------------------------- OpenAI option -----------------------
def llm_refine(ticker: str, raw: Dict) -> Dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return raw
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        prompt = f"""
너는 미국 주식 리서치 에이전트다. 반드시 첫 줄은 매수/매도/관망 중 하나로만 시작해라.
그 다음 줄부터 Seeking Alpha 관련 헤드라인, 미국 경제뉴스, 기업뉴스, 기술지표를 종합해 한국어로 짧고 전문적으로 판단해라.
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
    db_execute("""
    INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?,?)
    """, (ticker.upper(), today_key(), result["opinion"], result["summary"], float(result["score"]), json.dumps(result, ensure_ascii=False), dt.datetime.now().isoformat()))

def load_analysis(ticker: str, date: Optional[str] = None):
    rows = db_execute("SELECT opinion, summary, score, payload, created_at FROM analyses WHERE ticker=? AND trade_date=?", (ticker.upper(), date or today_key()), True)
    return rows[0] if rows else None

def run_analysis(ticker: str, use_llm=True, force=False):
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

# --------------------------- UI -------------------------------
def candle_chart(df: pd.DataFrame, ticker: str):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="Price"))
    for ma in ["MA20", "MA60", "MA120"]:
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], mode="lines", name=ma))
    fig.update_layout(title=f"{ticker} Daily Chart", height=520, margin=dict(l=10,r=10,t=40,b=10), xaxis_rangeslider_visible=False)
    return fig

def explain_chart(df: pd.DataFrame):
    d = df.tail(180)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d.index, y=d["RSI14"], mode="lines", name="RSI(14)"))
    fig.add_hline(y=70, line_dash="dash")
    fig.add_hline(y=30, line_dash="dash")
    fig.update_layout(title="에이전트 설명용 그래프: RSI", height=260, margin=dict(l=10,r=10,t=40,b=10))
    return fig

def watchlist_rows():
    rows = db_execute("SELECT ticker,name,group_name,updated_at FROM watchlist", fetch=True) or []
    def sort_key(row):
        score = get_today_score(row[0])
        # 점수가 없는 종목은 아래로 보내고, 점수가 높은 종목부터 표시합니다.
        return (score is None, -(score if score is not None else -9999), row[2], row[0])
    return sorted(rows, key=sort_key)

def add_watch(ticker, group):
    db_execute("INSERT OR REPLACE INTO watchlist VALUES(?,?,?,?)", (ticker.upper(), ticker.upper(), group, dt.datetime.now().isoformat()))

def remove_watch(ticker):
    db_execute("DELETE FROM watchlist WHERE ticker=?", (ticker.upper(),))


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
    tech_score = float(tech.get("score", 0))
    news_score = float(sent.get("score", 0))
    total_score = float(result.get("score", tech_score + news_score))
    reasons = tech.get("reasons", []) or []
    reason_text = ", ".join(reasons[:4]) if reasons else "확인 가능한 기술 근거가 부족합니다"
    return [
        f"{ticker}의 기술점수는 {tech_score:.1f}, 뉴스 키워드 점수는 {news_score:.1f}입니다.",
        f"핵심 기술 근거는 {reason_text}입니다.",
        f"시킹알파 관련 헤드라인, 미국 경제뉴스, 기업뉴스를 함께 보면 현재 점수는 {total_score:.1f}로 평가됩니다.",
        "최근 확인된 주요 헤드라인:",
    ]

init_db()
st.set_page_config(page_title="Stock Agent Pro", page_icon="📈", layout="wide")
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
    st.session_state.selected_ticker = "TSLA"
st.title("📈 Stock Agent Pro — 차트 + 매수/매도 에이전트")
st.caption("관심그룹은 매일 1회 자동 캐시 분석, 비관심 종목은 분석 버튼 클릭 시에만 분석됩니다. Seeking Alpha는 RSS/검색 헤드라인 기반으로 참고합니다.")

with st.sidebar:
    st.header("설정")
    use_llm = st.toggle("OpenAI로 의견 고도화", value=bool(os.getenv("OPENAI_API_KEY")))
    group = st.text_input("관심그룹 이름", "기본 관심그룹")
    add_t = st.text_input("관심 종목 추가", "TSLA")
    if st.button("관심그룹에 추가"):
        add_watch(add_t, group); st.success(f"{add_t.upper()} 추가")
    st.divider()
    rows = watchlist_rows()
    st.write("관심그룹")
    for t, name, g, upd in rows:
        score = get_today_score(t)
        col_a, col_b = st.columns([4, 1])
        if col_a.button(f"{g} · {t}", key=f"select_{g}_{t}", width="stretch"):
            st.session_state.selected_ticker = t
            st.rerun()
        col_b.markdown(score_badge_html(score), unsafe_allow_html=True)
        if st.button("삭제", key=f"del_{g}_{t}"):
            remove_watch(t); st.rerun()
    st.divider()
    if st.button("관심그룹 Daily 업그레이드 실행"):
        for t, *_ in rows:
            with st.spinner(f"{t} 분석 중..."):
                run_analysis(t, use_llm=use_llm, force=True)
        st.success("업데이트 완료")

left, right = st.columns([1.35, 0.9], gap="large")

with left:
    st.subheader("종목 검색 차트")
    ticker = st.text_input("티커 검색", value=st.session_state.selected_ticker).upper().strip()
    st.session_state.selected_ticker = ticker
    period = st.selectbox("기간", ["6mo", "1y", "2y", "5y"], index=0)
    df = fetch_price(ticker, period=period)
    if not df.empty:
        st.plotly_chart(candle_chart(df, ticker), width="stretch")
        st.subheader("에이전트 설명용 그래프")
        st.plotly_chart(explain_chart(df), width="stretch")
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

        st.markdown("**수집한 뉴스 헤드라인**")
        news = result.get("news", {})
        for section, title in [("seeking_alpha_signal", "Seeking Alpha"), ("us_economy_news", "미국 경제뉴스"), ("company_news", "기업뉴스")]:
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
