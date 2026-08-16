"""Independent reconciliation for the 2026-08-14 ETF flow snapshot.

The audit deliberately avoids the current published snapshot and the current
share-repair guard. It reads the pre-guard archived exchange universe for T and
T-1, combines it with an independent historical NAV panel, detects corporate
actions using the joint share/NAV discontinuity, and compares market scopes.
"""
from __future__ import annotations

import math
from datetime import date

import akshare as ak
import numpy as np
import pandas as pd
import requests

import update_daily as base

CUR = date(2026, 8, 14)
PREV = date(2026, 8, 13)
RAW_COMMIT = "d6cbb14dd70ad5ee27b0a47675a069c01803d9fe"
FACTORS = (0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0)


def archived_shares(day: date) -> pd.DataFrame:
    url = (
        "https://raw.githubusercontent.com/1581599248-bit/etf-flow-radar/"
        f"{RAW_COMMIT}/site/data/universe/{day.isoformat()}.json"
    )
    response = requests.get(url, timeout=(10, 45))
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("universe", [])
    out = pd.DataFrame(rows)
    required = {"code", "name", "shares", "exchange"}
    if out.empty or not required.issubset(out.columns):
        raise RuntimeError(f"archived universe missing required fields for {day}")
    out = out[["code", "name", "shares", "exchange"]].copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["shares"] = pd.to_numeric(out["shares"], errors="coerce")
    return out.dropna(subset=["shares"]).drop_duplicates("code", keep="last")


def ths_nav(day: date) -> pd.DataFrame:
    last_error: Exception | None = None
    for _ in range(3):
        try:
            raw = ak.fund_etf_category_ths(symbol="ETF", date=day.strftime("%Y%m%d"))
            out = raw[["基金代码", "基金名称", "当前-单位净值", "前一日-单位净值", "基金类型", "查询日期"]].copy()
            out.columns = ["code", "fund_name", "nav", "prev_nav", "fund_type", "query_date"]
            out["code"] = out["code"].astype(str).str.zfill(6)
            out["nav"] = pd.to_numeric(out["nav"], errors="coerce")
            out["prev_nav"] = pd.to_numeric(out["prev_nav"], errors="coerce")
            out["query_date"] = pd.to_datetime(out["query_date"], errors="coerce").dt.date
            out = out[out["query_date"] == day]
            if not out.empty:
                return out.drop_duplicates("code", keep="last")
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"THS NAV audit source unavailable: {last_error}")


def same_day_avg_prices(day: date, nav: pd.DataFrame) -> pd.DataFrame:
    try:
        spot = ak.fund_etf_spot_em().copy()
    except Exception:
        return pd.DataFrame(columns=["code", "avg_price", "price_type"])
    spot.columns = [str(c).strip() for c in spot.columns]
    if "数据日期" not in spot.columns:
        return pd.DataFrame(columns=["code", "avg_price", "price_type"])
    spot["_date"] = pd.to_datetime(spot["数据日期"], errors="coerce").dt.date
    spot = spot[spot["_date"] == day]
    if spot.empty:
        return pd.DataFrame(columns=["code", "avg_price", "price_type"])
    out = pd.DataFrame({
        "code": spot["代码"].astype(str).str.zfill(6),
        "close": pd.to_numeric(spot["最新价"], errors="coerce"),
        "volume": pd.to_numeric(spot["成交量"], errors="coerce"),
        "amount": pd.to_numeric(spot["成交额"], errors="coerce"),
    }).drop_duplicates("code", keep="last")
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_share = out["amount"] / out["volume"]
        raw_lot = out["amount"] / (out["volume"] * 100)
    close = out["close"].where(out["close"] > 0)
    choose_share = (raw_share / close - 1).abs() < (raw_lot / close - 1).abs()
    avg = raw_share.where(choose_share, raw_lot)
    avg = avg.where(avg.notna() & (avg > 0) & (avg / close).between(0.5, 2.0), close)
    out["avg_price"] = avg
    out = out.merge(nav[["code", "nav"]], on="code", how="left")
    nav_ok = out["nav"].notna() & (out["nav"] > 0)
    price_ok = out["avg_price"].notna() & (out["avg_price"] > 0)
    consistent = price_ok & (~nav_ok | ((out["avg_price"] / out["nav"] - 1).abs() <= 0.20))
    out["avg_price"] = out["avg_price"].where(consistent, out["nav"])
    out["price_type"] = np.where(consistent, "AVG_OR_CLOSE", "NAV_GUARD")
    return out[["code", "avg_price", "price_type"]]


def split_factor(prev_shares: float, cur_shares: float, prev_nav: float, nav: float) -> float | None:
    vals = (prev_shares, cur_shares, prev_nav, nav)
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in vals):
        return None
    share_ratio = cur_shares / prev_shares
    factor = min(FACTORS, key=lambda x: abs(share_ratio / x - 1))
    if abs(share_ratio / factor - 1) > 0.05:
        return None
    nav_ratio = nav / prev_nav
    if abs(nav_ratio / (1.0 / factor) - 1) > 0.12:
        return None
    return float(factor)


def scope_a_share_equity(row: pd.Series) -> bool:
    if str(row.get("fund_type", "")) != "股票型":
        return False
    combined = f"{row.get('name','')} {row.get('fund_name','')}"
    return not bool(base.EXCLUDE.search(combined))


def scope_classified(row: pd.Series) -> bool:
    if not scope_a_share_equity(row):
        return False
    return base.classify_etf(str(row.get("name", "")), str(row.get("fund_name", ""))) is not None


def summarize(label: str, frame: pd.DataFrame) -> None:
    print(f"AUDIT {label}: count={len(frame)} flow_avg={frame['flow_avg'].sum():.2f}bn flow_nav={frame['flow_nav'].sum():.2f}bn raw_nav={frame['raw_flow_nav'].sum():.2f}bn")
    print("  top inflow:")
    for r in frame.nlargest(8, "flow_avg")[["code", "name", "flow_avg", "delta_adj", "price"]].itertuples(index=False):
        print(f"    {r.code} {r.name}: {r.flow_avg:+.2f}bn delta={r.delta_adj/1e8:+.2f}e8 price={r.price:.4f}")
    print("  top outflow:")
    for r in frame.nsmallest(8, "flow_avg")[["code", "name", "flow_avg", "delta_adj", "price"]].itertuples(index=False):
        print(f"    {r.code} {r.name}: {r.flow_avg:+.2f}bn delta={r.delta_adj/1e8:+.2f}e8 price={r.price:.4f}")


def main() -> int:
    cur = archived_shares(CUR).rename(columns={"shares": "cur_shares"})
    prev = archived_shares(PREV)[["code", "shares"]].rename(columns={"shares": "prev_shares"})
    nav = ths_nav(CUR)
    px = same_day_avg_prices(CUR, nav)
    panel = cur.merge(prev, on="code", how="inner").merge(nav, on="code", how="left").merge(px, on="code", how="left")
    panel["price"] = panel["avg_price"].where(panel["avg_price"].notna() & (panel["avg_price"] > 0), panel["nav"])
    panel["factor"] = [
        split_factor(float(a), float(b), float(c), float(d))
        if pd.notna(c) and pd.notna(d) else None
        for a, b, c, d in zip(panel["prev_shares"], panel["cur_shares"], panel["prev_nav"], panel["nav"])
    ]
    panel["prev_shares_adj"] = panel["prev_shares"] * panel["factor"].fillna(1.0)
    panel["delta_raw"] = panel["cur_shares"] - panel["prev_shares"]
    panel["delta_adj"] = panel["cur_shares"] - panel["prev_shares_adj"]
    panel["raw_flow_nav"] = panel["delta_raw"] * panel["nav"] / 1e8
    panel["flow_nav"] = panel["delta_adj"] * panel["nav"] / 1e8
    panel["flow_avg"] = panel["delta_adj"] * panel["price"] / 1e8

    print(f"AUDIT exchange current={len(cur)} previous={len(prev)} matched={len(panel)} nav_coverage={panel['nav'].notna().sum()} price_coverage={panel['price'].notna().sum()}")
    actions = panel[panel["factor"].notna()].copy()
    print(f"AUDIT confirmed_corporate_actions={len(actions)}")
    for r in actions[["code", "name", "prev_shares", "cur_shares", "prev_nav", "nav", "factor", "flow_avg"]].itertuples(index=False):
        print(f"  action {r.code} {r.name}: factor={r.factor:g} shares={r.prev_shares:.0f}->{r.cur_shares:.0f} nav={r.prev_nav:.4f}->{r.nav:.4f} adjusted_flow={r.flow_avg:+.2f}bn")

    usable = panel[panel["price"].notna() & panel["nav"].notna()].copy()
    all_stock = usable[usable["fund_type"].astype(str).eq("股票型")].copy()
    ashare = usable[usable.apply(scope_a_share_equity, axis=1)].copy()
    classified = usable[usable.apply(scope_classified, axis=1)].copy()
    summarize("ALL_STOCK_ETF_THS", all_stock)
    summarize("A_SHARE_EQUITY", ashare)
    summarize("CURRENT_CLASSIFIED", classified)

    missing_class = ashare[~ashare.apply(scope_classified, axis=1)].copy()
    print(f"AUDIT ashare_unclassified_count={len(missing_class)} flow_avg={missing_class['flow_avg'].sum():+.2f}bn")
    for r in missing_class.reindex(missing_class["flow_avg"].abs().sort_values(ascending=False).index).head(15)[["code", "name", "fund_name", "flow_avg"]].itertuples(index=False):
        print(f"  unclassified {r.code} {r.name} / {r.fund_name}: {r.flow_avg:+.2f}bn")

    target = panel[panel["code"] == "588710"]
    if not target.empty:
        r = target.iloc[0]
        print("AUDIT 588710 " + " ".join([
            f"prev_shares={r.prev_shares:.0f}", f"cur_shares={r.cur_shares:.0f}",
            f"prev_nav={r.prev_nav:.4f}", f"nav={r.nav:.4f}", f"factor={r.factor}",
            f"raw_flow_nav={r.raw_flow_nav:+.2f}bn", f"adjusted_flow_avg={r.flow_avg:+.2f}bn",
            f"price={r.price:.4f}", f"price_type={r.price_type}",
        ]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
