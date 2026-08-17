"""ETF flow metric reconciliation for the 2026-08-17 review.

The production GitHub runner is intermittently blocked by historical exchange
endpoints, so this audit reuses immutable exchange-share files already archived
for 2026-08-13/14. It does not read published market totals. Exact-date THS NAV
and fund type are fetched independently.

Two variables are kept separate:
1. primary-market net subscription/redemption = comparable share change * NAV;
2. secondary-market main-order flow = an exchange-trading order-flow statistic.

Historical secondary order flow is read only from an immutable same-day file. A
current spot snapshot is never queried to reconstruct an old trading day.
All monetary output is in 亿元.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd

import update_daily as base

CUR = date(2026, 8, 14)
PREV = date(2026, 8, 13)
FACTORS = (0.2, 0.25, 1/3, 0.5, 2.0, 3.0, 4.0, 5.0)
WATCH = {"510300", "510500", "512100", "159915", "588000", "588170", "515880", "588710"}
ROOT = Path(__file__).resolve().parents[1]


def archived_shares(day: date) -> pd.DataFrame:
    path = ROOT / "site" / "data" / "universe" / f"{day.isoformat()}.json"
    payload = json.loads(path.read_text("utf-8"))
    out = pd.DataFrame(payload["universe"])
    required = {"code", "name", "shares", "exchange"}
    if out.empty or not required.issubset(out.columns):
        raise RuntimeError(f"archived exchange universe invalid for {day}")
    out = out[["code", "name", "shares", "exchange"]].copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["shares"] = pd.to_numeric(out["shares"], errors="coerce")
    return out.dropna(subset=["shares"]).drop_duplicates("code", keep="last")


def exact_nav(day: date) -> pd.DataFrame:
    raw = base.retry(
        f"THS exact ETF panel {day}",
        lambda: ak.fund_etf_category_ths(symbol="ETF", date=day.strftime("%Y%m%d")),
        attempts=3,
    )
    out = raw[["基金代码", "基金名称", "当前-单位净值", "前一日-单位净值", "基金类型", "查询日期"]].copy()
    out.columns = ["code", "fund_name", "nav", "prev_nav", "fund_type", "query_date"]
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["nav"] = pd.to_numeric(out["nav"], errors="coerce")
    out["prev_nav"] = pd.to_numeric(out["prev_nav"], errors="coerce")
    out["query_date"] = pd.to_datetime(out["query_date"], errors="coerce").dt.date
    return out[out["query_date"] == day].drop_duplicates("code", keep="last")


def split_factor(prev_shares: float, cur_shares: float, prev_nav: float, nav: float) -> float | None:
    vals = (prev_shares, cur_shares, prev_nav, nav)
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in vals):
        return None
    ratio = cur_shares / prev_shares
    factor = min(FACTORS, key=lambda x: abs(ratio / x - 1))
    if abs(ratio / factor - 1) > 0.05:
        return None
    if abs((nav / prev_nav) / (1 / factor) - 1) > 0.12:
        return None
    return float(factor)


def domestic_stock(row: pd.Series) -> bool:
    if str(row.get("fund_type", "")).strip() != "股票型":
        return False
    text = f"{row.get('name', '')} {row.get('fund_name', '')}"
    return not bool(base.EXCLUDE.search(text))


def secondary_order_flow(nav: pd.DataFrame, current: pd.DataFrame) -> None:
    path = ROOT / "site" / "data" / "order_flow" / f"{CUR.isoformat()}.json"
    if not path.exists():
        print(f"SECONDARY unavailable: no immutable same-day file for {CUR}")
        return
    payload = json.loads(path.read_text("utf-8"))
    if payload.get("tradeDate") != CUR.isoformat() or payload.get("metric") != "secondaryMarketMainOrderFlow":
        print(f"SECONDARY unavailable: invalid archived order-flow metadata for {CUR}")
        return
    s = pd.DataFrame(payload.get("etfs", []))
    if s.empty or not {"code", "mainOrderFlow1d"}.issubset(s.columns):
        print(f"SECONDARY unavailable: archived order-flow rows invalid for {CUR}")
        return
    s["code"] = s["code"].astype(str).str.zfill(6)
    s["secondary_order_flow"] = pd.to_numeric(s["mainOrderFlow1d"], errors="coerce")
    names = current[["code", "name"]].merge(nav[["code", "fund_name", "fund_type"]], on="code", how="left")
    x = s.dropna(subset=["secondary_order_flow"]).merge(names, on="code", how="inner")
    all_stock = x[x["fund_type"].astype(str).str.strip().eq("股票型")].copy()
    domestic = x[x.apply(domestic_stock, axis=1)].copy()
    print(
        f"SECONDARY {CUR} exact_count={len(x)} all_etf_main_order={x['secondary_order_flow'].sum():+.2f}亿 "
        f"stock_including_crossborder_count={len(all_stock)} stock_main_order={all_stock['secondary_order_flow'].sum():+.2f}亿 "
        f"domestic_stock_count={len(domestic)} domestic_main_order={domestic['secondary_order_flow'].sum():+.2f}亿"
    )


def main() -> int:
    cur = archived_shares(CUR).rename(columns={"shares": "cur_shares"})
    prev = archived_shares(PREV)[["code", "shares"]].rename(columns={"shares": "prev_shares"})
    nav = exact_nav(CUR)
    p = cur.merge(prev, on="code", how="inner").merge(nav, on="code", how="left")
    p["factor"] = [
        split_factor(float(a), float(b), float(c), float(d))
        if pd.notna(c) and pd.notna(d) else None
        for a, b, c, d in zip(p["prev_shares"], p["cur_shares"], p["prev_nav"], p["nav"])
    ]
    p["prev_adj"] = p["prev_shares"] * p["factor"].fillna(1.0)
    p["delta"] = p["cur_shares"] - p["prev_adj"]
    p["flow_nav"] = p["delta"] * p["nav"] / 1e8
    usable = p[p["nav"].notna() & (p["nav"] > 0)].copy()
    stock = usable[usable["fund_type"].astype(str).str.strip().eq("股票型")].copy()
    domestic = usable[usable.apply(domestic_stock, axis=1)].copy()

    print(
        f"PRIMARY {CUR} exchange={len(cur)} matched={len(p)} usable_nav={len(usable)} "
        f"all_etf_nav={usable['flow_nav'].sum():+.2f}亿 "
        f"stock_including_crossborder_count={len(stock)} stock_nav={stock['flow_nav'].sum():+.2f}亿 "
        f"domestic_stock_count={len(domestic)} domestic_stock_nav={domestic['flow_nav'].sum():+.2f}亿"
    )
    print("FUND_TYPE_TOTALS")
    for fund_type, group in usable.groupby(usable["fund_type"].astype(str).str.strip()):
        print(f"  {fund_type}: count={len(group)} flow_nav={group['flow_nav'].sum():+.2f}亿")

    actions = p[p["factor"].notna()]
    print(f"CORPORATE_ACTIONS count={len(actions)}")
    for r in actions[["code", "name", "factor", "prev_shares", "cur_shares", "prev_nav", "nav", "flow_nav"]].itertuples(index=False):
        print(
            f"  {r.code} {r.name} factor={r.factor:g} shares={r.prev_shares:.0f}->{r.cur_shares:.0f} "
            f"nav={r.prev_nav:.4f}->{r.nav:.4f} flow_nav={r.flow_nav:+.2f}亿"
        )
    for r in usable[usable["code"].isin(WATCH)].sort_values("code")[["code", "name", "delta", "nav", "flow_nav"]].itertuples(index=False):
        print(f"ETF {r.code} {r.name}: delta={r.delta/1e8:+.2f}亿份 nav={r.nav:.4f} flow_nav={r.flow_nav:+.2f}亿")

    secondary_order_flow(nav, cur.rename(columns={"cur_shares": "shares"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
