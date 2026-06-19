from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go
from core.indicators import enrich, add_hhll


def _rangebreaks():
    return [dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour")]


def price_chart(df: pd.DataFrame, title: str = "Price chart"):
    d = enrich(df)
    fig = go.Figure()
    if d.empty or len(d) < 2:
        fig.update_layout(title="데이터 없음", height=360, xaxis=dict(visible=False), yaxis=dict(visible=False))
        fig.add_annotation(text="가격 데이터를 가져오지 못했습니다.", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        return fig
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Price"))
    for col, name in [("EMA8","EMA8"),("EMA13","EMA13"),("MA21","MA21"),("MA55","MA55")]:
        if col in d:
            fig.add_trace(go.Scatter(x=d.index, y=d[col], name=name, mode="lines"))
    fig.update_layout(title=title, height=520, xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=40,b=10))
    fig.update_xaxes(rangebreaks=_rangebreaks())
    return fig


def hhll_chart(df: pd.DataFrame, title: str="HHLL"):
    d = add_hhll(df).dropna()
    fig = go.Figure()
    if d.empty or len(d) < 2:
        fig.update_layout(title="데이터 없음", height=320, xaxis=dict(visible=False), yaxis=dict(visible=False))
        fig.add_annotation(text="HHLL 데이터를 가져오지 못했습니다.", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        return fig
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="15분봉"))
    fig.add_trace(go.Scatter(x=d.index, y=d["HH20"], name="Highest High 20", mode="lines"))
    fig.add_trace(go.Scatter(x=d.index, y=d["LL10"], name="Lowest Low 10", mode="lines"))
    fig.add_trace(go.Scatter(x=d[d["HH20_BREAK"]].index, y=d[d["HH20_BREAK"]]["Close"], mode="markers", name="HH20 돌파", marker=dict(size=10, symbol="triangle-up")))
    fig.add_trace(go.Scatter(x=d[d["LL10_BREAK"]].index, y=d[d["LL10_BREAK"]]["Close"], mode="markers", name="LL10 이탈", marker=dict(size=10, symbol="triangle-down")))
    fig.update_layout(title=title, height=420, xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=40,b=10))
    fig.update_xaxes(rangebreaks=_rangebreaks())
    return fig
