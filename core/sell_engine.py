from __future__ import annotations
import numpy as np
import pandas as pd
from .indicators import enrich, add_hhll, technical_score_100


def grade(score: float) -> str:
    if score >= 90: return "AAA"
    if score >= 80: return "AA"
    if score >= 70: return "A"
    if score >= 60: return "B"
    if score >= 50: return "C"
    return "D"


def action_from_score(score: float, ll10_break: bool = False, below_ma55: bool = False) -> str:
    if ll10_break or score < 40:
        return "전량매도 검토"
    if score < 55:
        return "절반매도"
    if score < 65:
        return "비중축소"
    if score < 85:
        return "유지"
    return "추가매수"


def analyze_position(df_daily: pd.DataFrame, df_15m: pd.DataFrame | None = None, buy_price: float = 0.0, quantity: float = 0.0) -> dict:
    if df_daily is None or df_daily.empty:
        return {"AI의견":"관찰", "점수":50.0, "등급":"C", "현재가":None, "수익률%":0.0, "ATR손절":None, "추적손절":None, "터틀손절":None, "목표참고":None, "핵심근거":"가격 데이터 부족"}
    d = enrich(df_daily).dropna()
    if d.empty:
        return {"AI의견":"관찰", "점수":50.0, "등급":"C", "현재가":None, "수익률%":0.0, "ATR손절":None, "추적손절":None, "터틀손절":None, "목표참고":None, "핵심근거":"지표 데이터 부족"}
    score, reasons = technical_score_100(df_daily)
    price = float(d["Close"].iloc[-1])
    last = d.iloc[-1]
    h = add_hhll(df_15m if df_15m is not None and not df_15m.empty else df_daily).dropna()
    ll10 = None; ll10_break=False; hh20_break=False
    if not h.empty:
        hh20_break = bool(h["HH20_BREAK"].iloc[-1]) if "HH20_BREAK" in h else False
        ll10_break = bool(h["LL10_BREAK"].iloc[-1]) if "LL10_BREAK" in h else False
        ll10 = float(h["LL10"].iloc[-1]) if pd.notna(h["LL10"].iloc[-1]) else None
    if hh20_break:
        score += 6; reasons.append("HH20 돌파")
    if ll10_break:
        score -= 25; reasons.append("LL10 이탈")
    below_ma55 = bool(price < float(last.get("MA55", price)))
    if below_ma55:
        score -= 10; reasons.append("가격이 MA55 아래")
    if buy_price and buy_price > 0:
        pnl = (price / buy_price - 1) * 100
        if pnl > 20:
            score -= 4; reasons.append("수익 구간: 일부 이익실현 검토")
        elif pnl < -8:
            score -= 10; reasons.append("손실 확대 구간")
    else:
        pnl = 0.0
    score = float(max(0, min(100, round(score, 1))))
    atr = float(last.get("ATR14", np.nan)) if pd.notna(last.get("ATR14", np.nan)) else None
    atr_stop = round(price - atr * 2, 2) if atr else None
    trailing = round(d["Close"].tail(20).max() * 0.92, 2) if len(d) >= 20 else None
    target = round(price * 1.12, 2)
    action = action_from_score(score, ll10_break, below_ma55)
    return {
        "AI의견": action,
        "점수": score,
        "등급": grade(score),
        "현재가": round(price, 2),
        "수익률%": round(pnl, 2),
        "ATR손절": atr_stop,
        "추적손절": trailing,
        "터틀손절": round(ll10, 2) if ll10 else None,
        "목표참고": target,
        "핵심근거": " / ".join(reasons[:8])
    }
