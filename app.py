from __future__ import annotations
import datetime as dt
import pandas as pd
import streamlit as st

from market.symbols import UNIVERSE, SECTOR_ETFS, DEFAULT_HOLDINGS
from market.data import get_price, latest_price, company_name, norm_ticker
from core.indicators import technical_score_100, enrich
from core.sell_engine import analyze_position
from core.conviction import conviction_for
from storage.db import init_db, load_holdings, save_holdings, to_json, from_json_bytes, append_recommendations, load_recommendations
from broker.nh_import import parse_nh_balance
from ui.charts import price_chart, hhll_chart

st.set_page_config(page_title="Kappy Investment OS V15", layout="wide", page_icon="📈")

st.markdown("""
<style>
html, body, [class*="css"] {font-size: 14px;}
.block-container {padding-top: 2rem;}
.small-note {color:#6b7280; font-size:0.9rem;}
</style>
""", unsafe_allow_html=True)

init_db()


def ensure_default_holdings():
    df = load_holdings()
    if df.empty:
        save_holdings(pd.DataFrame(DEFAULT_HOLDINGS))


def holdings_with_values(df: pd.DataFrame, fetch: bool=False) -> pd.DataFrame:
    out = df.copy()
    vals=[]
    for _, r in out.iterrows():
        t = norm_ticker(r.get("ticker"))
        p = latest_price(t) if fetch and t != "SPACEX" else None
        vals.append(p)
    if fetch:
        out["current_price"] = vals
    else:
        out["current_price"] = None
    out["buy_amount"] = pd.to_numeric(out.get("buy_price",0), errors="coerce").fillna(0) * pd.to_numeric(out.get("quantity",0), errors="coerce").fillna(0)
    out["market_value"] = [((p or 0) * float(q or 0)) for p,q in zip(out["current_price"], out["quantity"])]
    out["pnl%"] = [round(((p/b-1)*100),2) if p and b else 0.0 for p,b in zip(out["current_price"], out["buy_price"])]
    return out


def analyze_holdings(df: pd.DataFrame, progress=True) -> pd.DataFrame:
    rows=[]
    bar = st.progress(0) if progress else None
    actual = df[df["actual"] == True] if "actual" in df else df
    total = max(1, len(actual))
    for i, (_, r) in enumerate(actual.iterrows()):
        t = norm_ticker(r.get("ticker"))
        if not t or t == "SPACEX":
            rows.append({"티커":t,"종목명":r.get("name",t),"AI의견":"장기보유 참고","등급":"-","점수":50,"현재가":None,"수익률%":0,"ATR손절":None,"추적손절":None,"터틀손절":None,"핵심근거":"비상장/수동관리"})
            continue
        d = get_price(t, "6mo", "1d", ttl=1800)
        h = get_price(t, "1mo", "15m", ttl=900)
        res = analyze_position(d, h, float(r.get("buy_price",0) or 0), float(r.get("quantity",0) or 0))
        rows.append({"티커":t,"종목명":r.get("name") or company_name(t), **res})
        if bar: bar.progress((i+1)/total)
    return pd.DataFrame(rows)



def render_sidebar():
    """V15.1 Dashboard Sidebar: 계좌 요약, 보유종목, 관심종목, 빠른 실행."""
    with st.sidebar:
        st.markdown("## 📈 Kappy OS")
        st.caption("보유종목·관심종목·오늘 할 일을 왼쪽에서 바로 확인합니다.")

        hdf = load_holdings()
        if hdf is None or hdf.empty:
            st.info("보유종목이 없습니다.")
        else:
            actual = hdf[hdf.get("actual", False) == True] if "actual" in hdf.columns else hdf
            buy_amount = pd.to_numeric(hdf.get("buy_price", 0), errors="coerce").fillna(0) * pd.to_numeric(hdf.get("quantity", 0), errors="coerce").fillna(0)
            st.metric("보유종목", len(actual))
            st.metric("총 매수금액", f"${buy_amount.sum():,.0f}")

            st.markdown("### ✅ 보유종목")
            if actual.empty:
                st.caption("실제투자 체크된 종목이 없습니다.")
            else:
                for _, r in actual.iterrows():
                    t = norm_ticker(r.get("ticker"))
                    name = r.get("name") or company_name(t)
                    label = f"{t} · {name}"
                    if st.button(label, key=f"side_hold_{t}", use_container_width=True):
                        st.session_state["chart_ticker"] = t
                        st.session_state["selected_ticker"] = t
                        st.toast(f"{t} 선택")

        st.divider()
        st.markdown("### ⭐ 관심종목")
        if "watchlist" not in st.session_state:
            st.session_state["watchlist"] = ["ARM", "AVGO", "DELL"]
        new_watch = st.text_input("관심종목 추가", key="side_new_watch", placeholder="예: PLTR")
        if st.button("관심종목 추가", key="side_add_watch", use_container_width=True):
            t = norm_ticker(new_watch)
            if t and t not in st.session_state["watchlist"]:
                st.session_state["watchlist"].append(t)
                st.toast(f"{t} 추가")
        for t in list(st.session_state["watchlist"]):
            c1, c2 = st.columns([4,1])
            if c1.button(f"{t} · {company_name(t)}", key=f"side_watch_{t}", use_container_width=True):
                st.session_state["chart_ticker"] = t
                st.session_state["selected_ticker"] = t
                st.toast(f"{t} 선택")
            if c2.button("×", key=f"side_del_{t}"):
                st.session_state["watchlist"].remove(t)
                st.rerun()

        st.divider()
        st.markdown("### 🤖 AI 빠른 실행")
        if st.button("오늘 브리핑", key="side_today", use_container_width=True):
            st.session_state["side_hint"] = "오늘 브리핑 탭에서 버튼을 눌러 보유종목을 분석하세요."
        if st.button("후보분석", key="side_scanner", use_container_width=True):
            st.session_state["side_hint"] = "후보 스캐너 탭에서 분석 종목 수를 선택하고 실행하세요."
        if st.button("Conviction Score", key="side_conviction", use_container_width=True):
            st.session_state["side_hint"] = "주식전망요약 탭에서 AI Conviction Score를 실행하세요."
        if st.session_state.get("side_hint"):
            st.info(st.session_state["side_hint"])


def tabs_header():
    st.title("📈 Kappy Investment OS V15 — 정식 아키텍처")
    st.caption("보유종목 중심 · 버튼 실행형 데이터 수집 · AI Conviction Score · 매도 타이밍 엔진 · SQLite 저장")

ensure_default_holdings()
render_sidebar()

tabs_header()
tabs = st.tabs(["오늘 브리핑", "보유종목", "종목 차트·에이전트", "후보 스캐너", "시장·섹터", "백테스트", "포트폴리오", "매매일지", "성과학습", "주식전망요약"])

# 01 Today
with tabs[0]:
    st.header("오늘 해야 할 일")
    st.caption("데이터 요청 과다를 막기 위해 버튼을 눌렀을 때만 보유종목을 분석합니다.")
    hdf = load_holdings()
    actual_count = int(hdf["actual"].sum()) if not hdf.empty else 0
    c1,c2,c3 = st.columns(3)
    c1.metric("실제 보유 종목", actual_count)
    c2.metric("시장 체제", "Risk On")
    c3.metric("권장 현금비중", "10%")
    if st.button("오늘 보유종목 AI 브리핑 생성", key="today_analyze"):
        result = analyze_holdings(hdf)
        st.session_state["today_result"] = result
    result = st.session_state.get("today_result")
    if isinstance(result, pd.DataFrame) and not result.empty:
        add = int((result["AI의견"] == "추가매수").sum())
        hold = int((result["AI의견"] == "유지").sum())
        reduce = int(result["AI의견"].isin(["비중축소","절반매도","전량매도 검토"]).sum())
        c1,c2,c3 = st.columns(3)
        c1.metric("추가매수 후보", add)
        c2.metric("유지", hold)
        c3.metric("매도/축소 검토", reduce)
        st.dataframe(result, use_container_width=True, hide_index=True)
        st.subheader("AI 요약")
        for _, r in result.iterrows():
            st.write(f"**{r['티커']} · {r['종목명']}**: {r['AI의견']} ({r['점수']}점, {r['등급']}) — {r['핵심근거']}")
    else:
        st.info("아직 오늘 브리핑을 생성하지 않았습니다.")

# 02 Holdings
with tabs[1]:
    st.header("보유종목 관리")
    st.caption("매수가와 수량만 입력하면 매수금액은 자동 계산됩니다. 저장하면 다음 접속 시 자동 복원됩니다.")
    hdf = load_holdings()
    col1, col2, col3 = st.columns([1,1,1])
    with col1:
        if st.button("기본 실제 투자 포트폴리오 불러오기", key="load_defaults"):
            save_holdings(pd.DataFrame(DEFAULT_HOLDINGS)); st.rerun()
    with col2:
        backup = to_json(hdf)
        st.download_button("보유종목 JSON 백업", backup, file_name="kappy_holdings_backup.json", mime="application/json")
    with col3:
        restore = st.file_uploader("JSON/CSV 복원", type=["json","csv"], key="restore_holdings")
    if restore:
        try:
            if restore.name.lower().endswith("json"):
                rdf = from_json_bytes(restore.read())
            else:
                rdf = pd.read_csv(restore)
            save_holdings(rdf); st.success("복원 완료"); st.rerun()
        except Exception as e:
            st.error(f"복원 실패: {e}")
    nh = st.file_uploader("NH 나무증권 종합잔고(.xls HTML) 가져오기", type=["xls","html","htm"], key="nh_upload_v15")
    if nh:
        try:
            ndf = parse_nh_balance(nh)
            if ndf.empty:
                st.warning("인식된 보유종목이 없습니다. NH 파일 형식이 다를 수 있습니다.")
            else:
                st.write("인식 결과")
                st.dataframe(ndf, use_container_width=True, hide_index=True)
                if st.button("NH 잔고를 보유종목에 저장", key="save_nh"):
                    save_holdings(ndf); st.success("저장 완료"); st.rerun()
        except Exception as e:
            st.error(f"NH 잔고 읽기 실패: {e}")
    st.divider()
    hdf = load_holdings()
    edited = st.data_editor(
        hdf,
        num_rows="dynamic",
        use_container_width=True,
        key="holdings_editor_v15",
        column_config={
            "actual": st.column_config.CheckboxColumn("실제투자"),
            "ticker": "티커", "name":"종목명",
            "buy_price": st.column_config.NumberColumn("매수가", format="%.4f"),
            "quantity": st.column_config.NumberColumn("수량", format="%.4f"),
            "buy_date":"매수일", "memo":"메모"
        }
    )
    preview = edited.copy()
    preview["매수금액"] = pd.to_numeric(preview["buy_price"], errors="coerce").fillna(0) * pd.to_numeric(preview["quantity"], errors="coerce").fillna(0)
    st.write("자동 계산 미리보기")
    st.dataframe(preview[["actual","ticker","name","buy_price","quantity","매수금액","buy_date","memo"]], use_container_width=True, hide_index=True)
    if st.button("보유종목 저장", key="save_holdings_main"):
        save_holdings(edited); st.success("저장되었습니다.")

# 03 Chart Agent
with tabs[2]:
    st.header("종목 차트·에이전트")
    col1, col2, col3 = st.columns([2,1,1])
    ticker = col1.text_input("티커", value=st.session_state.get("chart_ticker", "MU"), key="chart_ticker").upper()
    period = col2.selectbox("기간", ["6mo","1mo","1wk","1d"], index=0, key="chart_period")
    interval = col3.selectbox("봉", ["1d","30m","15m"], index=0, key="chart_interval")
    if st.button("차트/분석 실행", key="chart_run"):
        df = get_price(ticker, period, interval, ttl=600)
        st.plotly_chart(price_chart(df, f"{ticker} Chart"), use_container_width=True)
        score, reasons = technical_score_100(df)
        st.metric("기술점수", f"{score}/100")
        st.write(" / ".join(reasons))
        h = get_price(ticker, "1mo", "15m", ttl=600)
        st.plotly_chart(hhll_chart(h, f"{ticker} HHLL 15분봉"), use_container_width=True)

# 04 Scanner
with tabs[3]:
    st.header("후보 스캐너")
    st.caption("처음에는 20개 이하 권장. 버튼을 누를 때만 순차 분석합니다.")
    n = st.selectbox("분석 종목 수", [10,20,50], index=1, key="scanner_n")
    show_n = st.selectbox("추천 표시 수", [10,20,50], index=1, key="scanner_show")
    if st.button("후보 분석 실행", key="scanner_run"):
        rows=[]; bar=st.progress(0)
        for i,t in enumerate(UNIVERSE[:n]):
            df = get_price(t, "6mo", "1d", ttl=1800)
            score, reasons = technical_score_100(df)
            price = float(df["Close"].dropna().iloc[-1]) if df is not None and not df.empty else None
            rows.append({"티커":t,"종목명":company_name(t),"점수":score,"현재가":price,"근거":" / ".join(reasons[:5])})
            bar.progress((i+1)/n)
        rdf = pd.DataFrame(rows).sort_values("점수", ascending=False).head(show_n)
        st.session_state["scanner_result"] = rdf
        append_recommendations(rdf.to_dict("records"), "scanner")
    rdf = st.session_state.get("scanner_result")
    if isinstance(rdf, pd.DataFrame):
        st.dataframe(rdf, use_container_width=True, hide_index=True)

# 05 Market Sector
with tabs[4]:
    st.header("시장·섹터 로테이션")
    if st.button("시장·섹터 분석 실행", key="sector_run"):
        rows=[]
        for name,t in SECTOR_ETFS.items():
            df = get_price(t, "6mo", "1d", ttl=3600)
            score, reasons = technical_score_100(df)
            rows.append({"섹터":name,"ETF":t,"점수":score,"상태":"강세" if score>=70 else "중립" if score>=55 else "약세","근거":" / ".join(reasons[:4])})
        sdf = pd.DataFrame(rows).sort_values("점수", ascending=False)
        st.dataframe(sdf, use_container_width=True, hide_index=True)
    else:
        st.info("버튼을 눌러 시장·섹터 데이터를 가져옵니다.")

# 06 Backtest
with tabs[5]:
    st.header("간단 백테스트")
    st.caption("EMA8 > EMA13 > MA21 > MA55 조건에서 10거래일 뒤 성과를 단순 검증합니다.")
    bn = st.selectbox("검증 종목 수", [10,20,50], index=1, key="backtest_n")
    if st.button("백테스트 실행", key="backtest_run"):
        rows=[]; bar=st.progress(0)
        for i,t in enumerate(UNIVERSE[:bn]):
            df = enrich(get_price(t, "2y", "1d", ttl=7200)).dropna()
            trades=[]
            if len(df) > 80:
                cond = (df["EMA8"]>df["EMA13"]) & (df["EMA13"]>df["MA21"]) & (df["MA21"]>df["MA55"])
                idxs = list(df[cond].index)
                for idx in idxs[::10]:
                    loc = df.index.get_loc(idx)
                    if loc+10 < len(df):
                        trades.append((df["Close"].iloc[loc+10]/df["Close"].iloc[loc]-1)*100)
            if trades:
                win = sum(x>0 for x in trades)/len(trades)*100
                avg = sum(trades)/len(trades)
                pf = sum(x for x in trades if x>0)/abs(sum(x for x in trades if x<0) or 1)
            else:
                win=avg=pf=0
            rows.append({"티커":t,"종목명":company_name(t),"거래수":len(trades),"승률%":round(win,1),"평균수익%":round(avg,2),"Profit Factor":round(pf,2),"검증점수":round(win/5+avg,2)})
            bar.progress((i+1)/bn)
        st.dataframe(pd.DataFrame(rows).sort_values("검증점수", ascending=False), use_container_width=True, hide_index=True)

# 07 Portfolio
with tabs[6]:
    st.header("포트폴리오")
    hdf = load_holdings()
    fetch = st.button("현재가 반영", key="portfolio_fetch")
    vdf = holdings_with_values(hdf, fetch=fetch)
    st.dataframe(vdf, use_container_width=True, hide_index=True)
    st.caption("Streamlit Cloud에서는 SQLite가 재배포 때 초기화될 수 있으므로 JSON 백업도 함께 보관하세요.")

# 08 Trade log
with tabs[7]:
    st.header("매매일지")
    st.info("V15 정식 구조에서는 매매일지 DB 테이블이 준비되어 있습니다. 다음 단계에서 입력/청산/성과학습을 더 고도화합니다.")

# 09 Learning
with tabs[8]:
    st.header("성과학습")
    rec = load_recommendations()
    if rec.empty:
        st.info("아직 저장된 추천 기록이 없습니다.")
    else:
        st.dataframe(rec, use_container_width=True, hide_index=True)

# 10 Conviction
with tabs[9]:
    st.header("주식전망요약 · AI Conviction Score")
    st.write("각 종목을 최근 뉴스, 목표주가 여력, 기술추세, AI 밸류체인, 자금유입 강도로 100점 평가합니다.")
    tickers = st.text_area("분석할 종목", value="AMD, ARM, AVGO, DELL, INTC, MU, SOXL, TSLA", key="conviction_tickers")
    if st.button("AI Conviction Score 분석 실행", key="conviction_run"):
        rows=[]; detail=[]; symbols=[x.strip().upper() for x in tickers.split(",") if x.strip()]
        bar=st.progress(0)
        for i,t in enumerate(symbols):
            r = conviction_for(t)
            detail.append((t, r.pop("헤드라인", [])))
            rows.append(r); bar.progress((i+1)/max(1,len(symbols)))
        cdf = pd.DataFrame(rows).sort_values("AI Conviction Score", ascending=False)
        st.dataframe(cdf, use_container_width=True, hide_index=True)
        append_recommendations(cdf.to_dict("records"), "conviction")
        st.subheader("최근 확인된 주요 헤드라인")
        for t, heads in detail:
            if heads:
                st.write(f"**{t}**")
                for h in heads[:3]: st.write("- "+h)
