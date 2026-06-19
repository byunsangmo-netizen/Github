from __future__ import annotations
import numpy as np
import pandas as pd


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] for c in out.columns]
    for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["Close"])
    if out.empty:
        return out
    out["EMA8"] = out["Close"].ewm(span=8, adjust=False).mean()
    out["EMA13"] = out["Close"].ewm(span=13, adjust=False).mean()
    out["MA21"] = out["Close"].rolling(21).mean()
    out["MA55"] = out["Close"].rolling(55).mean()
    out["RSI14"] = rsi(out["Close"], 14)
    high_low = out["High"] - out["Low"]
    high_close = (out["High"] - out["Close"].shift()).abs()
    low_close = (out["Low"] - out["Close"].shift()).abs()
    out["TR"] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    out["ATR14"] = out["TR"].rolling(14).mean()
    if "Volume" in out.columns:
        out["VOL_MA20"] = out["Volume"].rolling(20).mean()
        out["VOL_RATIO"] = out["Volume"] / out["VOL_MA20"].replace(0, np.nan)
        direction = np.sign(out["Close"].diff()).fillna(0)
        out["OBV"] = (direction * out["Volume"].fillna(0)).cumsum()
        out["OBV_MA10"] = out["OBV"].rolling(10).mean()
    return out


def add_hhll(df: pd.DataFrame, hh: int = 20, ll: int = 10) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] for c in out.columns]
    out["HH20"] = out["High"].rolling(hh).max().shift(1)
    out["LL10"] = out["Low"].rolling(ll).min().shift(1)
    out["HH20_BREAK"] = out["Close"] > out["HH20"]
    out["LL10_BREAK"] = out["Close"] < out["LL10"]
    return out


def technical_score_100(df: pd.DataFrame) -> tuple[float, list[str]]:
    d = enrich(df).dropna()
    if d.empty or len(d) < 55:
        return 50.0, ["가격 데이터 부족"]
    last = d.iloc[-1]
    score = 50.0
    reasons: list[str] = []
    if last["Close"] > last["EMA8"]:
        score += 8; reasons.append("가격이 EMA8 위")
    else:
        score -= 8; reasons.append("가격이 EMA8 아래")
    if last["EMA8"] > last["EMA13"]:
        score += 8; reasons.append("EMA8 > EMA13")
    else:
        score -= 6; reasons.append("EMA8 < EMA13")
    if last["EMA13"] > last["MA21"]:
        score += 8; reasons.append("EMA13 > MA21")
    else:
        score -= 6; reasons.append("EMA13 < MA21")
    if last["MA21"] > last["MA55"]:
        score += 10; reasons.append("MA21 > MA55")
    else:
        score -= 10; reasons.append("MA21 < MA55")
    r = float(last.get("RSI14", 50))
    if 45 <= r <= 68:
        score += 8; reasons.append(f"RSI {r:.1f}: 양호")
    elif r > 75:
        score -= 7; reasons.append(f"RSI {r:.1f}: 과열")
    elif r < 40:
        score -= 7; reasons.append(f"RSI {r:.1f}: 약세")
    else:
        reasons.append(f"RSI {r:.1f}: 중립")
    vr = float(last.get("VOL_RATIO", np.nan)) if "VOL_RATIO" in d.columns else np.nan
    if not np.isnan(vr):
        if vr >= 1.8:
            score += 8; reasons.append(f"거래량 {vr:.1f}배")
        elif vr >= 1.2:
            score += 4; reasons.append(f"거래량 {vr:.1f}배")
        elif vr < 0.7:
            score -= 3; reasons.append(f"거래량 {vr:.1f}배: 약함")
    return float(max(0, min(100, round(score, 1)))), reasons
