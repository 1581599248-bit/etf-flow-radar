"""Persist same-day ETF trading-flow facts before the provider date rolls."""
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
    spot = base.retry("Eastmoney ETF same-day trading flow", ak.fund_etf_spot_em, attempts=3)
    spot.columns = [str(c).strip() for c in spot.columns]
    required = {
        "代码", "名称", "主力净流入-净额", "成交额", "外盘", "内盘", "数据日期",
        "最新份额", "更新时间",
    }
    if not required.issubset(spot.columns):
        raise ValueError(f"ETF spot schema changed; missing={sorted(required-set(spot.columns))}")

    frame = spot[[
        "代码", "名称", "主力净流入-净额", "成交额", "外盘", "内盘", "数据日期",
        "最新份额", "更新时间",
    ]].copy()
    frame.columns = [
        "code", "name", "main_yuan", "amount_yuan", "outer", "inner", "data_date",
        "latest_shares", "share_updated_at",
    ]
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["data_date"] = pd.to_datetime(frame["data_date"], errors="coerce").dt.date
    for col in ("main_yuan", "amount_yuan", "outer", "inner", "latest_shares"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame[frame["data_date"] == day].dropna(subset=["main_yuan", "amount_yuan", "outer", "inner"])
    frame = frame.drop_duplicates("code", keep="last")

    directional = frame["outer"] + frame["inner"]
    frame = frame[directional > 0].copy()
    directional = frame["outer"] + frame["inner"]
    frame["trade_in_yuan"] = frame["amount_yuan"] * frame["outer"] / directional
    frame["trade_out_yuan"] = frame["amount_yuan"] * frame["inner"] / directional
    frame["trade_net_yuan"] = frame["trade_in_yuan"] - frame["trade_out_yuan"]

    if len(frame) < MIN_ROWS:
        raise ValueError(f"same-day ETF trading-flow coverage too low: {len(frame)}")
    if int((frame["amount_yuan"] > 0).sum()) < MIN_ROWS:
        raise ValueError("same-day ETF trading amounts are not populated")

    share_rows = int((frame["latest_shares"] > 0).sum())
    return {
        "schemaVersion": 2,
        "tradeDate": day.isoformat(),
        "generatedAt": datetime.now(CN).isoformat(timespec="seconds"),
        "metric": "secondaryMarketETFTradingFlow",
        "source": "Eastmoney fund_etf_spot_em 成交额 + 外盘/内盘",
        "definition": "当日交易资金净额按外盘/内盘主动成交量占比拆分成交额后，以主动买入金额减主动卖出金额计算；与ETF份额变化分开。",
        "shareObservation": {
            "source": "Eastmoney fund_etf_spot_em 最新份额",
            "status": "available" if share_rows >= MIN_ROWS else "partial",
            "rowCount": share_rows,
            "definition": "与盘中成交快照同时冻结的供应商最新份额原始观测，仅作为官方交易所日终份额不可达时的审计/备援证据；不自动替代官方主口径。",
        },
        "etfCount": int(len(frame)),
        "totalTradeInflow1d": round(float(frame["trade_in_yuan"].sum()) / 1e8, 2),
        "totalTradeOutflow1d": round(float(frame["trade_out_yuan"].sum()) / 1e8, 2),
        "totalTradeNetFlow1d": round(float(frame["trade_net_yuan"].sum()) / 1e8, 2),
        "totalMainOrderFlow1d": round(float(frame["main_yuan"].sum()) / 1e8, 2),
        "etfs": [{
            "code": str(r.code), "name": str(r.name),
            "tradeInflow1d": round(float(r.trade_in_yuan) / 1e8, 4),
            "tradeOutflow1d": round(float(r.trade_out_yuan) / 1e8, 4),
            "tradeNetFlow1d": round(float(r.trade_net_yuan) / 1e8, 4),
            "mainOrderFlow1d": round(float(r.main_yuan) / 1e8, 4),
            "amount": round(float(r.amount_yuan) / 1e8, 4),
            "latestShares": round(float(r.latest_shares), 4) if pd.notna(r.latest_shares) else None,
            "shareDataDate": r.data_date.isoformat() if pd.notna(r.data_date) else None,
            "shareUpdatedAt": None if pd.isna(r.share_updated_at) else str(r.share_updated_at),
        } for r in frame.itertuples(index=False)],
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
        if (
            existing.get("metric") == "secondaryMarketETFTradingFlow"
            and int(existing.get("etfCount", 0)) >= MIN_ROWS
            and existing.get("shareObservation", {}).get("rowCount", 0) >= MIN_ROWS
        ):
            print(f"trading-flow + share snapshot already exists: {target}")
            return 0
        if existing.get("metric") == "secondaryMarketMainOrderFlow" and day != datetime.now(CN).date():
            print(f"legacy order-flow snapshot retained: {target}")
            return 0
    try:
        snapshot = build_snapshot(day)
    except ValueError as exc:
        if "not an exchange trading session" in str(exc):
            print(f"trading-flow capture skipped: {exc}")
            return 0
        raise
    path = publish(snapshot)
    print(
        f"captured {snapshot['etfCount']} ETF trading-flow rows and "
        f"{snapshot['shareObservation']['rowCount']} share observations: {path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
