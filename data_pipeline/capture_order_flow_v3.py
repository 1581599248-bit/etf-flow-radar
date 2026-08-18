"""Persist same-day ETF secondary-market trading-direction facts.

This module intentionally avoids the words "net inflow/outflow" for the
outer/inner-volume calculation.  Every secondary-market transaction has both a
buyer and a seller; the derived statistic is an aggressor-side imbalance, not
new money entering or leaving the ETF market.
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
METRIC = "secondaryMarketAggressorImbalanceEstimate"


def _is_exchange_session(day: date) -> bool:
    calendar = base.retry("A-share trading calendar", ak.tool_trade_date_hist_sina, attempts=3)
    if "trade_date" not in calendar.columns:
        raise ValueError("trading calendar schema changed")
    dates = set(pd.to_datetime(calendar["trade_date"], errors="coerce").dt.date.dropna())
    return day in dates


def build_snapshot(day: date) -> dict:
    if not _is_exchange_session(day):
        raise ValueError(f"{day.isoformat()} is not an exchange trading session")

    spot = base.retry("Eastmoney ETF same-day secondary trading statistics", ak.fund_etf_spot_em, attempts=3)
    spot.columns = [str(c).strip() for c in spot.columns]
    required = {"代码", "名称", "主力净流入-净额", "成交额", "外盘", "内盘", "数据日期"}
    if not required.issubset(spot.columns):
        raise ValueError(f"ETF spot schema changed; missing={sorted(required-set(spot.columns))}")

    frame = spot[["代码", "名称", "主力净流入-净额", "成交额", "外盘", "内盘", "数据日期"]].copy()
    frame.columns = ["code", "name", "vendor_main_yuan", "amount_yuan", "outer", "inner", "data_date"]
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["data_date"] = pd.to_datetime(frame["data_date"], errors="coerce").dt.date
    for col in ("vendor_main_yuan", "amount_yuan", "outer", "inner"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame[frame["data_date"] == day].dropna(
        subset=["vendor_main_yuan", "amount_yuan", "outer", "inner"]
    )
    frame = frame.drop_duplicates("code", keep="last")

    directional = frame["outer"] + frame["inner"]
    frame = frame[directional > 0].copy()
    directional = frame["outer"] + frame["inner"]
    frame["buy_initiated_yuan"] = frame["amount_yuan"] * frame["outer"] / directional
    frame["sell_initiated_yuan"] = frame["amount_yuan"] * frame["inner"] / directional
    frame["aggressor_imbalance_yuan"] = frame["buy_initiated_yuan"] - frame["sell_initiated_yuan"]

    if len(frame) < MIN_ROWS:
        raise ValueError(f"same-day ETF secondary-trading coverage too low: {len(frame)}")
    if int((frame["amount_yuan"] > 0).sum()) < MIN_ROWS:
        raise ValueError("same-day ETF trading amounts are not populated")

    return {
        "schemaVersion": 3,
        "tradeDate": day.isoformat(),
        "generatedAt": datetime.now(CN).isoformat(timespec="seconds"),
        "metric": METRIC,
        "displayName": "ETF主动成交方向差额（估算）",
        "source": "东方财富 fund_etf_spot_em：成交额、外盘、内盘；另保存行情商“主力净流入-净额”原字段",
        "definition": (
            "按同日ETF成交额与外盘/内盘主动成交量占比估算主动买入成交额和主动卖出成交额，"
            "两者之差为主动成交方向差额。该指标不代表市场净新增资金，不是ETF一级市场净申购/赎回。"
        ),
        "etfCount": int(len(frame)),
        "totalBuyInitiatedEstimate1d": round(float(frame["buy_initiated_yuan"].sum()) / 1e8, 2),
        "totalSellInitiatedEstimate1d": round(float(frame["sell_initiated_yuan"].sum()) / 1e8, 2),
        "totalAggressorImbalance1d": round(float(frame["aggressor_imbalance_yuan"].sum()) / 1e8, 2),
        "totalVendorMainOrderNet1d": round(float(frame["vendor_main_yuan"].sum()) / 1e8, 2),
        "etfs": [
            {
                "code": str(r.code),
                "name": str(r.name),
                "buyInitiatedEstimate1d": round(float(r.buy_initiated_yuan) / 1e8, 4),
                "sellInitiatedEstimate1d": round(float(r.sell_initiated_yuan) / 1e8, 4),
                "aggressorImbalance1d": round(float(r.aggressor_imbalance_yuan) / 1e8, 4),
                "vendorMainOrderNet1d": round(float(r.vendor_main_yuan) / 1e8, 4),
                "amount": round(float(r.amount_yuan) / 1e8, 4),
            }
            for r in frame.itertuples(index=False)
        ],
    }


def publish(snapshot: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{snapshot['tradeDate']}.json"
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
        if existing.get("metric") == METRIC and int(existing.get("etfCount", 0)) >= MIN_ROWS:
            print(f"secondary-trading snapshot already exists: {target}")
            return 0
        # Never overwrite a historical snapshot just because its older schema
        # used different terminology.  Production can read both versions.
        if day != datetime.now(CN).date() and int(existing.get("etfCount", 0)) >= MIN_ROWS:
            print(f"historical secondary-trading snapshot retained: {target}")
            return 0
    try:
        snapshot = build_snapshot(day)
    except ValueError as exc:
        if "not an exchange trading session" in str(exc):
            print(f"secondary-trading capture skipped: {exc}")
            return 0
        raise
    path = publish(snapshot)
    print(f"captured {snapshot['etfCount']} ETF secondary-trading rows: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
