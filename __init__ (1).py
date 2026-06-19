from __future__ import annotations
import re
import pandas as pd
from bs4 import BeautifulSoup

# NH/Namuh 종합잔고 HTML(.xls) importer
# - NH file stores foreign-stock purchase amount in KRW, while current price is often shown in USD.
# - We therefore convert avg buy price for US stocks back to USD using USD/KRW from the same file.

ISIN_TO_TICKER = {
    "US0079031078": "AMD",
    "US0420682058": "ARM",
    "US11135F1012": "AVGO",
    "US24703L2025": "DELL",
    "US25459W4583": "SOXL",
    "US4581401001": "INTC",
    "US5951121038": "MU",
    "US0378331005": "AAPL",
    "US5949181045": "MSFT",
    "US67066G1040": "NVDA",
    "US88160R1014": "TSLA",
    "US68389X1054": "ORCL",
    "US02079K3059": "GOOGL",
    "US02079K1079": "GOOG",
    "US30303M1027": "META",
    "US7475251036": "QCOM",
    "US17275R1023": "CSCO",
}

NAME_TO_TICKER = {
    "삼성전자": "005930.KS",
    "AMD": "AMD",
    "어드밴스드": "AMD",
    "암 홀딩스": "ARM",
    "브로드컴": "AVGO",
    "델테크놀로지": "DELL",
    "디렉시온 반도체": "SOXL",
    "인텔": "INTC",
    "마이크론": "MU",
    "테슬라": "TSLA",
    "오라클": "ORCL",
    "엔비디아": "NVDA",
}

SKIP_TYPES = ("예수금", "외화예수금", "자유약정형 RP", "CMA", "RP")


def _decode(data: bytes) -> str:
    for enc in ["utf-8", "cp949", "euc-kr", "latin1"]:
        try:
            return data.decode(enc)
        except Exception:
            pass
    return data.decode("utf-8", errors="ignore")


def _num(x) -> float:
    s = re.sub(r"[^0-9.\-]", "", str(x or ""))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except Exception:
        return 0.0


def _clean_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", "", str(name or "")).strip()
    return name or ""


def _ticker_from(product_name: str, product_code: str) -> tuple[str, str]:
    code = str(product_code or "").strip().upper()
    name = str(product_name or "").strip()
    if code in ISIN_TO_TICKER:
        return ISIN_TO_TICKER[code], "USD"
    if re.fullmatch(r"\d{6}", code):
        # default to KOSPI suffix; user can edit to .KQ if needed.
        return f"{code}.KS", "KRW"
    for k, v in NAME_TO_TICKER.items():
        if k.upper() in name.upper():
            return v, "USD" if not v.endswith((".KS", ".KQ")) else "KRW"
    # Last-resort ticker-like extraction from product code/name
    m = re.search(r"\b[A-Z]{1,5}\b", code)
    if m and m.group(0) not in {"USD", "KRW", "ETF", "ETN"}:
        return m.group(0), "USD"
    m = re.search(r"\b[A-Z]{1,5}\b", name)
    if m and m.group(0) not in {"USD", "KRW", "ETF", "ETN"}:
        return m.group(0), "USD"
    return "", ""


def _extract_usdkrw(rows: list[list[str]]) -> float:
    # NH foreign cash line often has: 외화예수금 / USD / USD / ... / 수량 / 현재가(USD/KRW)
    for cells in rows:
        joined = " ".join(cells)
        if "USD" in joined and ("외화예수금" in joined or "달러" in joined):
            nums = [_num(c) for c in cells]
            # Prefer an exchange-rate looking value, usually 1000~2500.
            candidates = [n for n in nums if 800 <= n <= 3000]
            if candidates:
                return candidates[-1]
    return 0.0


def parse_nh_balance(uploaded_file) -> pd.DataFrame:
    data = uploaded_file.read() if hasattr(uploaded_file, "read") else uploaded_file
    html = _decode(data)
    soup = BeautifulSoup(html, "html.parser")

    all_rows: list[list[str]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if cells:
            all_rows.append(cells)

    usdkrw = _extract_usdkrw(all_rows)
    parsed = []

    # Preferred format from NH 종합잔고:
    # No, 잔고유형, 상품명, 상품코드, 구분, 수량, 현재가, 매입금액, 평가금액, 평가손익, 수익률
    for cells in all_rows:
        if len(cells) < 10:
            continue
        if cells[0] == "No" or "상품명" in cells:
            continue
        balance_type = cells[1].strip()
        product_name = cells[2].strip()
        product_code = cells[3].strip()
        if any(s in balance_type for s in SKIP_TYPES) or any(s in product_name for s in SKIP_TYPES):
            continue
        ticker, currency = _ticker_from(product_name, product_code)
        if not ticker:
            continue

        qty = _num(cells[5])
        current_price = _num(cells[6])
        buy_amount_raw = _num(cells[7])  # usually KRW for NH total acquisition amount
        eval_amount_raw = _num(cells[8])
        pnl_raw = _num(cells[9])
        ret_raw = _num(cells[10]) if len(cells) > 10 else 0.0
        if qty <= 0:
            continue

        if currency == "USD":
            fx = usdkrw or (eval_amount_raw / (qty * current_price) if current_price > 0 else 0)
            buy_price = buy_amount_raw / qty / fx if fx else 0.0
            buy_amount = buy_price * qty
            memo = f"NH import · 원화매입 {buy_amount_raw:,.0f}원 · 환율 {fx:,.2f}"
            name = _clean_name(product_name)
        else:
            buy_price = buy_amount_raw / qty if qty else 0.0
            buy_amount = buy_amount_raw
            memo = "NH import"
            name = _clean_name(product_name)

        parsed.append({
            "actual": True,
            "ticker": ticker,
            "name": name or ticker,
            "currency": currency or "USD",
            "buy_price": round(float(buy_price), 4),
            "quantity": float(qty),
            "buy_amount": round(float(buy_amount), 4),
            "current_price_import": float(current_price),
            "market_value_import": float(eval_amount_raw),
            "pnl_import": float(pnl_raw),
            "return_import": float(ret_raw),
            "buy_date": "",
            "memo": memo,
        })

    if not parsed:
        return pd.DataFrame(columns=["actual","ticker","name","currency","buy_price","quantity","buy_amount","buy_date","memo"])

    df = pd.DataFrame(parsed).drop_duplicates(subset=["ticker"], keep="last")
    return df
