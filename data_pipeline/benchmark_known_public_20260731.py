"""Validate exchange-share timing against a published 2026-07-31 benchmark.

Public reference points available in indexed media:
- iFinD: stock ETF -243.75亿元; 159915 -54.90, 588000 -43.94,
  515880 -18.92亿元.
- Wind/China Fund: stock ETF including cross-border about -250.50亿元.

This script is diagnostic: if the upstream historical endpoint is temporarily
blocked it prints UNAVAILABLE rather than affecting production data.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import akshare as ak
import pandas as pd

import update_daily as base
import update_daily_resilient as resilient

DAY = date(2026, 7, 31)
FACTORS = (0.2, 0.25, 1/3, 0.5, 2.0, 3.0, 4.0, 5.0)
WATCH = {"159915", "588000", "515880"}


def nav_panel(day: date) -> pd.DataFrame:
    raw = base.retry(
        f"THS exact panel {day}",
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


def previous(day: date) -> tuple[date, pd.DataFrame]:
    d = day - timedelta(days=1)
    while (day - d).days < 10:
        if d.weekday() <= 4:
            try:
                f = base.fetch_exchange_shares(d)
                if len(f) >= base.MIN_MARKET_ETFS:
                    return d, f
            except Exception:
                pass
        d -= timedelta(days=1)
    raise RuntimeError("previous exchange session unavailable")


def factor(prev_shares: float, cur_shares: float, prev_nav: float, nav: float) -> float:
    vals = (prev_shares, cur_shares, prev_nav, nav)
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in vals):
        return 1.0
    ratio = cur_shares / prev_shares
    f = min(FACTORS, key=lambda x: abs(ratio / x - 1))
    if abs(ratio / f - 1) <= .05 and abs((nav / prev_nav) / (1 / f) - 1) <= .12:
        return float(f)
    return 1.0


def main() -> int:
    resilient.install_resilient_sources()
    try:
        cur = base.fetch_exchange_shares(DAY).rename(columns={"shares": "cur"})
        prev_day, p0 = previous(DAY)
        p = p0[["code", "shares"]].rename(columns={"shares": "prev"})
        nav = nav_panel(DAY)
    except Exception as exc:
        print(f"KNOWN_PUBLIC {DAY} UNAVAILABLE: {exc}")
        return 0

    x = cur.merge(p, on="code").merge(nav, on="code", how="left")
    x["factor"] = [factor(a, b, c, d) if pd.notna(c) and pd.notna(d) else 1.0
                   for a, b, c, d in zip(x["prev"], x["cur"], x["prev_nav"], x["nav"])]
    x["delta"] = x["cur"] - x["prev"] * x["factor"]
    x["flow_nav"] = x["delta"] * x["nav"] / 1e8
    stock = x[x["fund_type"].astype(str).str.strip().eq("股票型") & x["nav"].notna()].copy()
    domestic = stock[~stock.apply(lambda r: bool(base.EXCLUDE.search(f"{r['name']} {r['fund_name']}")), axis=1)].copy()
    print(
        f"KNOWN_PUBLIC {DAY} prev={prev_day} stock_all_nav={stock['flow_nav'].sum():+.2f}亿 "
        f"domestic_nav={domestic['flow_nav'].sum():+.2f}亿 "
        f"public_iFinD_stock=-243.75亿 public_Wind_stock_incl_crossborder=-250.50亿"
    )
    for r in stock[stock["code"].isin(WATCH)][["code", "name", "delta", "nav", "flow_nav"]].sort_values("code").itertuples(index=False):
        print(f"  {r.code} {r.name}: delta={r.delta/1e8:+.2f}亿份 nav={r.nav:.4f} flow_nav={r.flow_nav:+.2f}亿")
    print("  public singles: 159915=-54.90亿 588000=-43.94亿 515880=-18.92亿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
