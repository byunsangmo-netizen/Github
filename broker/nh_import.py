from __future__ import annotations
import re
import pandas as pd
from bs4 import BeautifulSoup


def _decode(data: bytes) -> str:
    for enc in ["utf-8", "euc-kr", "cp949", "latin1"]:
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


def parse_nh_balance(uploaded_file) -> pd.DataFrame:
    data = uploaded_file.read() if hasattr(uploaded_file, "read") else uploaded_file
    html = _decode(data)
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 4:
            continue
        joined = " ".join(cells)
        # detect symbol-like data: Korean 6-digit or US uppercase ticker
        code = None
        for c in cells:
            m = re.search(r"\b\d{6}\b", c)
            if m:
                code = m.group(0); break
            m = re.search(r"\b[A-Z]{1,5}\b", c)
            if m and m.group(0) not in {"USD","KRW","ETF","ETN"}:
                code = m.group(0); break
        if not code:
            continue
        # name: first non-numeric not equal code
        name = code
        for c in cells:
            if code in c and len(c) > len(code):
                name = c.replace(code, "").strip() or code; break
            if not re.fullmatch(r"[0-9,\.\-\s%]+", c) and code not in c and len(c) > 1:
                name = c; break
        nums = [_num(c) for c in cells]
        nums = [n for n in nums if n != 0]
        qty = nums[0] if nums else 0
        buy_price = nums[1] if len(nums) > 1 else 0
        # heuristic swap when price likely before qty
        if qty > 100000 and buy_price < 10000:
            qty, buy_price = buy_price, qty
        rows.append({"actual": True, "ticker": code, "name": name, "buy_price": float(buy_price), "quantity": float(qty), "buy_date": "", "memo": "NH import"})
    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker"], keep="last") if rows else pd.DataFrame(columns=["actual","ticker","name","buy_price","quantity","buy_date","memo"])
    return df
