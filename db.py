from __future__ import annotations
import os, time, hashlib
import pandas as pd
import yfinance as yf
from .symbols import NAME_FALLBACK

CACHE_DIR = "cache_prices"
os.makedirs(CACHE_DIR, exist_ok=True)


def norm_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def company_name(ticker: str) -> str:
    t = norm_ticker(ticker)
    if t in NAME_FALLBACK:
        return NAME_FALLBACK[t]
    try:
        info = yf.Ticker(t).fast_info
    except Exception:
        info = None
    try:
        name = yf.Ticker(t).info.get("shortName")
        return name or t
    except Exception:
        return t


def _cache_path(ticker: str, period: str, interval: str) -> str:
    key = hashlib.md5(f"{ticker}_{period}_{interval}".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.pkl")


def get_price(ticker: str, period: str = "6mo", interval: str = "1d", ttl: int = 1800) -> pd.DataFrame:
    ticker = norm_ticker(ticker)
    if ticker in ("", "SPACEX"):
        return pd.DataFrame()
    path = _cache_path(ticker, period, interval)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        try:
            return pd.read_pickle(path)
        except Exception:
            pass
    try:
        df = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False, threads=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            df.to_pickle(path)
            return df
    except Exception:
        if os.path.exists(path):
            try:
                return pd.read_pickle(path)
            except Exception:
                return pd.DataFrame()
    return pd.DataFrame()


def latest_price(ticker: str) -> float | None:
    df = get_price(ticker, period="5d", interval="30m", ttl=600)
    if df is None or df.empty:
        df = get_price(ticker, period="1mo", interval="1d", ttl=1800)
    if df is None or df.empty:
        return None
    try:
        return float(df["Close"].dropna().iloc[-1])
    except Exception:
        return None
