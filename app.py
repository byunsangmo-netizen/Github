from __future__ import annotations
import os, json, sqlite3, datetime as dt, time, hashlib
from typing import List, Dict

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go



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

@st.cache_data(ttl=86400, show_spinner=False)
def get_us_name(ticker: str) -> str:
    t = str(ticker).upper().strip()
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

DB_PATH = "stock_agent_v13.db"

st.set_page_config(page_title="Stock Agent Pro V13", page_icon="📈", layout="wide")

CSS = """
<style>
html, body, [class*="css"] { font-size: 0.88rem; }
.block-container { padding-top: 1.4rem; }
.small-badge {border-radius:999px; padding:2px 8px; background:#eef2ff; display:inline-block; margin-left:6px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

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

def add_watchlist(ticker: str, group="기본 관심그룹"):
    t = normalize_ticker(ticker)
    if not t: return
    q("INSERT OR REPLACE INTO watchlist(ticker,name,group_name,updated_at) VALUES(?,?,?,?)", (t, get_us_name(t), group, now()))

def watchlist_df() -> pd.DataFrame:
    rows = q("SELECT ticker,name,group_name,updated_at FROM watchlist ORDER BY group_name,ticker", fetch=True) or []
    return pd.DataFrame(rows, columns=["티커","종목명","그룹","업데이트"])

def holdings_df() -> pd.DataFrame:
    rows = q("SELECT ticker,name,invested,buy_price,buy_amount,quantity,source,memo,updated_at FROM portfolio_holdings ORDER BY invested DESC,ticker", fetch=True) or []
    df = pd.DataFrame(rows, columns=["티커","종목명","실제투자","매수가","매수금액","수량","소스","메모","업데이트"])
    if not df.empty:
        df["실제투자"] = df["실제투자"].astype(bool)
    return df

def save_holdings(df: pd.DataFrame):
    if df is None or df.empty: return
    for _, r in df.iterrows():
        t = normalize_ticker(r.get("티커"))
        if not t: continue
        name = r.get("종목명") or get_us_name(t)
        invested = 1 if bool(r.get("실제투자")) else 0
        buy_price = float(r.get("매수가") or 0)
        buy_amount = float(r.get("매수금액") or 0)
        qty = float(r.get("수량") or 0)
        if qty <= 0 and buy_price > 0 and buy_amount > 0:
            qty = buy_amount / buy_price
        q("""INSERT OR REPLACE INTO portfolio_holdings
             (ticker,name,invested,buy_price,buy_amount,quantity,source,memo,updated_at)
             VALUES(?,?,?,?,?,?,?,?,?)""", (t, name, invested, buy_price, buy_amount, qty, r.get("소스", ""), r.get("메모", ""), now()))
        if invested:
            add_watchlist(t, "매수 종목")

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
        sig=sell_signal(daily, intraday, float(r.get("매수가") or 0))
        current=sig.get("현재가") or 0
        buy_price=float(r.get("매수가") or 0)
        amount=float(r.get("매수금액") or 0)
        pnl=(current/buy_price-1)*100 if current and buy_price else 0
        rows.append({"티커":t,"종목명":r.get("종목명") or get_us_name(t),"현재가":current,"매수가":buy_price,"매수금액":amount,"수익률%":round(pnl,2),"의견":sig["의견"],"매도점수":sig["점수"],"손절참고":sig["손절참고"],"목표참고":sig["목표참고"],"핵심근거":sig["근거"]})
    return pd.DataFrame(rows)

# ----------------------------- Sidebar -----------------------------
st.sidebar.header("설정")
st.sidebar.caption("V13은 자동 대량 요청을 피하고, 버튼을 누를 때만 데이터를 수집합니다.")
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
st.title("📈 Stock Agent Pro V13 — 매도 타이밍·포트폴리오 관리 OS")
st.caption("자동 데이터 요청을 최소화했습니다. 후보 분석, 현 상황 분석, 백테스트는 버튼을 눌렀을 때만 실행됩니다.")

tabs = st.tabs(["종목 차트·에이전트", "후보 스캐너", "시장·섹터", "백테스트", "포트폴리오·매도관리", "매매일지", "성과학습"])

# ----------------------------- Chart tab -----------------------------
with tabs[0]:
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
                opinion="매수" if sc>=2.5 else "매도" if sc<=-1.5 else "관망"
                st.subheader(opinion)
                st.metric("기술점수", sc)
                st.write(f"핵심 기술 근거는 {', '.join(reasons[:5])}입니다.")
                st.caption("뉴스·옵션 데이터는 자동 요청하지 않습니다. 필요 시 후보 스캐너/시장 탭에서 별도 실행하세요.")
    else:
        st.info("티커 입력 후 '현 종목 분석'을 누르세요. 앱 시작 시 자동 다운로드하지 않습니다.")

# ----------------------------- Scanner -----------------------------
with tabs[1]:
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
with tabs[2]:
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
with tabs[3]:
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
with tabs[4]:
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
        edit_cols=["실제투자","티커","종목명","매수가","매수금액","수량","손절참고","목표참고","소스","메모"]
        # merge optional stop/target from session plan if missing in DB display
        for col in ["손절참고","목표참고"]:
            if col not in hdf.columns: hdf[col]=0.0
        edited=st.data_editor(hdf[[c for c in edit_cols if c in hdf.columns]], width="stretch", hide_index=True, num_rows="dynamic", key="holdings_editor")
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
            ba=c3.number_input("매수금액", min_value=0.0, value=0.0, step=100.0)
            inv=c4.checkbox("실제투자", value=True)
            if st.form_submit_button("보유 종목 추가") and mt:
                qty=ba/bp if bp>0 and ba>0 else 0
                save_holdings(pd.DataFrame([{"실제투자":inv,"티커":mt,"종목명":get_us_name(mt),"매수가":bp,"매수금액":ba,"수량":qty,"소스":"직접입력","메모":""}]))
                st.rerun()

# ----------------------------- Journal -----------------------------
with tabs[5]:
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
with tabs[6]:
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