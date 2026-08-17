"""Persist same-day ETF secondary-market order flow before the date rolls.

The overnight primary-market job cannot reconstruct historical order-flow fields
from a current spot snapshot. This lightweight collector therefore runs shortly
after the A-share close and writes an immutable per-trade-date fact file. It is
strictly separate from ETF creation/redemption data.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

import update_daily as base

CN = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "data" / "order_flow"
MIN_ROWS = 500


def _is_exchange_session(day: date) -> bool:
    calendar = base.retry("A-share trading calendar", ak.tool_trade_date_hist_sina, attempts=3)
    if "trade_date" not in calendar.columns:
        raise ValueError("trading calendar schema changed")
    dates = set(pd.to_datetime(calendar["trade_date"], errors="coerce").dt.date.dropna())
    return day in dates


def build_snapshot(day: date) -> dict:
    if not _is_exchange_session(day):
        raise ValueError(f"{day.isoformat()} is not an exchange trading session")
    spot = base.retry("Eastmoney ETF same-day order flow", ak.fund_etf_spot_em, attempts=3)
    spot.columns = [str(c).strip() for c in spot.columns]
    required = {"代码", "名称", "主力净流入-净额", "成交额", "数据日期"}
    if not required.issubset(spot.columns):
        raise ValueError(f"ETF spot schema changed; missing={sorted(required-set(spot.columns))}")

    frame = spot[["代码", "名称", "主力净流入-净额", "成交额", "数据日期"]].copy()
    frame.columns = ["code", "name", "main_order_flow_yuan", "amount_yuan", "data_date"]
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["data_date"] = pd.to_datetime(frame["data_date"], errors="coerce").dt.date
    frame["main_order_flow_yuan"] = pd.to_numeric(frame["main_order_flow_yuan"], errors="coerce")
    frame["amount_yuan"] = pd.to_numeric(frame["amount_yuan"], errors="coerce")
    frame = frame[frame["data_date"] == day].dropna(subset=["main_order_flow_yuan", "amount_yuan"])
    frame = frame.drop_duplicates("code", keep="last")
    if len(frame) < MIN_ROWS:
        raise ValueError(f"same-day ETF order-flow coverage too low: {len(frame)}")
    if int((frame["amount_yuan"] > 0).sum()) < MIN_ROWS:
        raise ValueError("same-day ETF trading amounts are not populated")

    return {
        "schemaVersion": 1,
        "tradeDate": day.isoformat(),
        "generatedAt": datetime.now(CN).isoformat(timespec="seconds"),
        "metric": "secondaryMarketMainOrderFlow",
        "source": "Eastmoney fund_etf_spot_em 主力净流入-净额",
        "definition": "ETF二级市场成交中的主力订单净流入；不是ETF申购/赎回。",
        "etfCount": int(len(frame)),
        "totalMainOrderFlow1d": round(float(frame["main_order_flow_yuan"].sum()) / 1e8, 2),
        "etfs": [
            {
                "code": str(r.code),
                "name": str(r.name),
                "mainOrderFlow1d": round(float(r.main_order_flow_yuan) / 1e8, 4),
                "amount": round(float(r.amount_yuan) / 1e8, 4),
            }
            for r in frame.itertuples(index=False)
        ],
    }


def publish(snapshot: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    day = snapshot["tradeDate"]
    target = OUT / f"{day}.json"
    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    target.write_text(text, "utf-8")
    (OUT / "latest.json").write_text(text, "utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="trade date YYYY-MM-DD; defaults to China local date")
    args = parser.parse_args()
    day = date.fromisoformat(args.date) if args.date else datetime.now(CN).date()
    target = OUT / f"{day.isoformat()}.json"
    if target.exists():
        existing = json.loads(target.read_text("utf-8"))
        if existing.get("metric") == "secondaryMarketMainOrderFlow" and int(existing.get("etfCount", 0)) >= MIN_ROWS:
            print(f"order-flow snapshot already exists: {target}")
            return 0
    try:
        snapshot = build_snapshot(day)
    except ValueError as exc:
        if "not an exchange trading session" in str(exc):
            print(f"order-flow capture skipped: {exc}")
            return 0
        raise
    path = publish(snapshot)
    print(f"captured {snapshot['etfCount']} ETF order-flow rows: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
