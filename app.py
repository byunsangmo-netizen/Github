from __future__ import annotations
import sqlite3, json
from pathlib import Path
import pandas as pd

DB_PATH = Path("kappy_investment_os.db")


def connect():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    con = connect(); cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS holdings(
        ticker TEXT PRIMARY KEY,
        name TEXT,
        actual INTEGER DEFAULT 1,
        buy_price REAL DEFAULT 0,
        quantity REAL DEFAULT 0,
        buy_date TEXT DEFAULT '',
        memo TEXT DEFAULT '',
        currency TEXT DEFAULT 'USD'
    )""")
    try:
        cur.execute("ALTER TABLE holdings ADD COLUMN currency TEXT DEFAULT 'USD'")
    except Exception:
        pass
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        ticker TEXT,
        name TEXT,
        side TEXT,
        price REAL,
        quantity REAL,
        memo TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS recommendations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        ticker TEXT,
        name TEXT,
        score REAL,
        opinion TEXT,
        source TEXT
    )""")
    con.commit(); con.close()


def load_holdings() -> pd.DataFrame:
    init_db(); con = connect()
    df = pd.read_sql_query("SELECT actual, ticker, name, COALESCE(currency, 'USD') AS currency, buy_price, quantity, buy_date, memo FROM holdings ORDER BY ticker", con)
    con.close()
    if df.empty:
        return pd.DataFrame(columns=["actual","ticker","name","currency","buy_price","quantity","buy_date","memo"])
    df["actual"] = df["actual"].astype(bool)
    return df


def save_holdings(df: pd.DataFrame):
    init_db(); con = connect(); cur = con.cursor()
    cur.execute("DELETE FROM holdings")
    for _, r in df.iterrows():
        ticker = str(r.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        cur.execute("""INSERT OR REPLACE INTO holdings(ticker,name,actual,buy_price,quantity,buy_date,memo,currency)
        VALUES(?,?,?,?,?,?,?,?)""", (
            ticker, str(r.get("name", ticker)), int(bool(r.get("actual", True))),
            float(r.get("buy_price", 0) or 0), float(r.get("quantity", 0) or 0),
            str(r.get("buy_date", "") or ""), str(r.get("memo", "") or ""),
            str(r.get("currency", "USD") or "USD")
        ))
    con.commit(); con.close()


def append_recommendations(rows: list[dict], source: str):
    import datetime as dt
    init_db(); con = connect(); cur = con.cursor(); ts = dt.datetime.now().isoformat(timespec="seconds")
    for r in rows:
        cur.execute("INSERT INTO recommendations(ts,ticker,name,score,opinion,source) VALUES(?,?,?,?,?,?)", (
            ts, r.get("티커") or r.get("ticker"), r.get("종목명") or r.get("name"),
            float(r.get("점수", r.get("AI Conviction Score", 0)) or 0), r.get("의견", ""), source
        ))
    con.commit(); con.close()


def load_recommendations(limit:int=200) -> pd.DataFrame:
    init_db(); con=connect()
    df = pd.read_sql_query(f"SELECT * FROM recommendations ORDER BY id DESC LIMIT {int(limit)}", con)
    con.close(); return df


def to_json(df: pd.DataFrame) -> str:
    return df.to_json(orient="records", force_ascii=False, indent=2)


def from_json_bytes(data: bytes) -> pd.DataFrame:
    obj = json.loads(data.decode("utf-8"))
    return pd.DataFrame(obj)
