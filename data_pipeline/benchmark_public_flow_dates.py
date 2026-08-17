"""Cross-date benchmark of the ETF primary-market flow convention.

This script is deliberately separate from the published snapshot.  It rebuilds
several historical dates from exchange end-of-day shares and an exact-date NAV /
fund-type panel, then prints totals and selected single-ETF flows that can be
compared with public Wind / Choice / iFinD reports.

Public benchmark references used during the 2026-08-17 methodology review:
- 2026-07-13 Choice: domestic stock ETF about +597.04 bn CNY.
- 2026-07-14 Choice/iFinD: stock ETF about +185.71/+191.56 bn CNY.
- 2026-07-17 Wind: stock ETF incl. cross-border about +758.67 bn CNY.
- 2026-07-30 iFinD: stock ETF about +404.92 bn CNY.
- 2026-07-31 iFinD: stock ETF about -243.75 bn CNY; Wind stock ETF incl.
  cross-border about -250.50 bn CNY.

All printed monetary values are亿元, not CNY bn despite the historical variable
name used elsewhere in the project.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import akshare as ak
import pandas as pd

import update_daily as base
import update_daily_resilient as resilient

DATES = [
    date(2026, 7, 13),
    date(2026, 7, 14),
    date(2026, 7, 17),
    date(2026, 7, 30),
    date(2026, 7, 31),
    date(2026, 8, 14),
]
FACTORS = (0.2, 0.25, 1/3, 0.5, 2.0, 3.0, 4.0, 5.0)
WATCH = {"510300", "510500", "512100", "159915", "588000", "588170", "515880", "588710"}


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


def previous_session(day: date) -> tuple[date, pd.DataFrame]:
    candidate = day - timedelta(days=1)
    while True:
        if candidate.weekday() <= 4:
            try:
                frame = base.fetch_exchange_shares(candidate)
                if len(frame) >= base.MIN_MARKET_ETFS:
                    return candidate, frame
            except Exception:
                pass
        candidate -= timedelta(days=1)
        if (day - candidate).days > 10:
            raise RuntimeError(f"cannot resolve previous session before {day}")


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


def run_day(day: date) -> None:
    cur = base.fetch_exchange_shares(day).rename(columns={"shares": "cur_shares"})
    prev_day, prev0 = previous_session(day)
    prev = prev0[["code", "shares"]].rename(columns={"shares": "prev_shares"})
    nav = exact_nav(day)
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
    all_stock = usable[usable["fund_type"].astype(str).str.strip().eq("股票型")].copy()
    domestic = usable[usable.apply(domestic_stock, axis=1)].copy()

    print(
        f"BENCH {day} prev={prev_day} exchange={len(cur)} matched={len(p)} "
        f"all_stock_count={len(all_stock)} all_stock_nav={all_stock['flow_nav'].sum():+.2f}亿 "
        f"domestic_count={len(domestic)} domestic_nav={domestic['flow_nav'].sum():+.2f}亿"
    )
    actions = p[p["factor"].notna()]
    for r in actions[["code", "name", "factor", "prev_shares", "cur_shares", "prev_nav", "nav", "flow_nav"]].itertuples(index=False):
        print(
            f"  ACTION {r.code} {r.name} factor={r.factor:g} "
            f"shares={r.prev_shares:.0f}->{r.cur_shares:.0f} nav={r.prev_nav:.4f}->{r.nav:.4f} "
            f"flow_nav={r.flow_nav:+.2f}亿"
        )
    watched = usable[usable["code"].isin(WATCH)].sort_values("code")
    for r in watched[["code", "name", "delta", "nav", "flow_nav"]].itertuples(index=False):
        print(f"  ETF {r.code} {r.name}: delta={r.delta/1e8:+.2f}亿份 nav={r.nav:.4f} flow_nav={r.flow_nav:+.2f}亿")


def main() -> int:
    resilient.install_resilient_sources()
    for day in DATES:
        run_day(day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
