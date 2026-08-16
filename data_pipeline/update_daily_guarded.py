"""Safety wrapper for the ETF flow pipeline.

This module keeps the existing client-facing schema/layout intact while adding
three data-integrity guards before publication:

1. The Eastmoney real-time snapshot is only used when its ``数据日期`` matches
   the requested trade date. Historical rebuilds therefore cannot accidentally
   use a later trading day's price.
2. Official SSE/SZSE end-of-day shares are cross-checked against Eastmoney's
   independent ``最新份额`` field. A row is repaired only when the official row
   shows an extreme one-day jump, the secondary source strongly disagrees with
   the official current value, and the secondary value remains close to the
   previous official value. Otherwise the official source remains authoritative.
3. Candidate ETF split/consolidation events are confirmed by an inverse price
   discontinuity before historical shares are restated. Unresolved single-ETF
   flows large enough to dominate the whole market fail closed instead of being
   published as verified data.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

import update_daily as base

_ORIG_FETCH_SHARE_WINDOW = base.fetch_share_window
_ORIG_BUILD_SNAPSHOT = base.build_snapshot

_SPOT_CACHE: pd.DataFrame | None = None
_SPOT_ERROR: str | None = None
_SHARE_AUDIT: dict[str, Any] = {"status": "not_run", "repaired": []}
_PRICE_GUARDS: list[dict[str, Any]] = []
_CORPORATE_ACTIONS: list[dict[str, Any]] = []

MIN_SECONDARY_COMMON = 100
MAX_SECONDARY_MEDIAN_REL_ERROR = 0.08
EXTREME_SHARE_JUMP = 0.35
SECONDARY_CURRENT_DISAGREEMENT = 0.15
SECONDARY_PREVIOUS_CONTINUITY = 0.10
PRICE_NAV_MAX_DEVIATION = 0.20
FLOW_REVIEW_ABS_BN = 50.0
FLOW_REVIEW_PRIOR_AUM_RATIO = 0.50
FLOW_HARD_STOP_ABS_BN = 100.0
FLOW_HARD_STOP_PRIOR_AUM_RATIO = 0.75


def _reset_run_state() -> None:
    global _SHARE_AUDIT, _PRICE_GUARDS, _CORPORATE_ACTIONS
    _SHARE_AUDIT = {"status": "not_run", "repaired": []}
    _PRICE_GUARDS = []
    _CORPORATE_ACTIONS = []


def _get_spot() -> pd.DataFrame:
    global _SPOT_CACHE, _SPOT_ERROR
    if _SPOT_CACHE is not None:
        return _SPOT_CACHE.copy()
    try:
        spot = base.retry("Eastmoney ETF spot with share audit", base.ak.fund_etf_spot_em, attempts=3)
        spot.columns = [str(c).strip() for c in spot.columns]
        if "代码" not in spot.columns:
            raise ValueError("Eastmoney ETF spot omitted 代码")
        spot = spot.copy()
        spot["代码"] = spot["代码"].astype(str).str.zfill(6)
        if "数据日期" in spot.columns:
            spot["_data_date"] = pd.to_datetime(spot["数据日期"], errors="coerce").dt.date
        else:
            spot["_data_date"] = pd.NaT
        _SPOT_CACHE = spot
        _SPOT_ERROR = None
    except Exception as exc:
        _SPOT_ERROR = str(exc)
        _SPOT_CACHE = pd.DataFrame()
    return _SPOT_CACHE.copy()


def infer_secondary_share_scale(
    official: pd.DataFrame,
    secondary: pd.DataFrame,
    *,
    minimum_common: int = MIN_SECONDARY_COMMON,
) -> tuple[float | None, float | None, int]:
    """Infer the unit multiplier of Eastmoney's ``最新份额`` field."""
    left = official[["code", "shares"]].copy()
    right = secondary[["code", "secondary_shares_raw"]].copy()
    merged = left.merge(right, on="code", how="inner")
    merged["shares"] = pd.to_numeric(merged["shares"], errors="coerce")
    merged["secondary_shares_raw"] = pd.to_numeric(merged["secondary_shares_raw"], errors="coerce")
    merged = merged[(merged["shares"] > 0) & (merged["secondary_shares_raw"] > 0)].dropna()
    common_count = int(len(merged))
    if common_count < minimum_common:
        return None, None, common_count

    candidates = (1.0, 100.0, 10_000.0, 1_000_000.0, 100_000_000.0)
    best_scale: float | None = None
    best_error = math.inf
    for scale in candidates:
        normalized = merged["secondary_shares_raw"] * scale
        rel = (normalized / merged["shares"] - 1).abs().replace([np.inf, -np.inf], np.nan).dropna()
        if rel.empty:
            continue
        error = float(rel.median())
        if error < best_error:
            best_scale, best_error = scale, error
    if best_scale is None or best_error > MAX_SECONDARY_MEDIAN_REL_ERROR:
        return None, (None if not math.isfinite(best_error) else best_error), common_count
    return best_scale, best_error, common_count


def repair_current_shares(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    secondary: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Repair only strongly evidenced row-level share glitches."""
    result = current.copy()
    scale, median_error, common_count = infer_secondary_share_scale(current, secondary)
    audit: dict[str, Any] = {
        "status": "usable" if scale is not None else "unusable",
        "scale": scale,
        "medianRelativeError": None if median_error is None else round(float(median_error), 6),
        "commonCount": common_count,
        "repaired": [],
    }
    if scale is None:
        return result, audit

    sec = secondary[["code", "secondary_shares_raw"]].copy()
    sec["secondary_shares"] = pd.to_numeric(sec["secondary_shares_raw"], errors="coerce") * scale
    prev = previous[["code", "shares"]].rename(columns={"shares": "previous_shares"})
    joined = result[["code", "name", "shares"]].merge(prev, on="code", how="left").merge(
        sec[["code", "secondary_shares"]], on="code", how="left"
    )
    for row in joined.itertuples(index=False):
        cur = float(row.shares) if pd.notna(row.shares) else math.nan
        prv = float(row.previous_shares) if pd.notna(row.previous_shares) else math.nan
        alt = float(row.secondary_shares) if pd.notna(row.secondary_shares) else math.nan
        if not all(math.isfinite(x) and x > 0 for x in (cur, prv, alt)):
            continue
        jump = cur / prv - 1
        current_gap = alt / cur - 1
        previous_gap = alt / prv - 1
        if (
            abs(jump) >= EXTREME_SHARE_JUMP
            and abs(current_gap) >= SECONDARY_CURRENT_DISAGREEMENT
            and abs(previous_gap) <= SECONDARY_PREVIOUS_CONTINUITY
        ):
            result.loc[result["code"] == row.code, "shares"] = alt
            audit["repaired"].append({
                "code": str(row.code),
                "name": str(row.name),
                "officialShares": round(cur, 2),
                "secondaryShares": round(alt, 2),
                "previousOfficialShares": round(prv, 2),
                "officialJumpPct": round(jump * 100, 2),
            })
    return result, audit


def _secondary_for_day(day: date) -> pd.DataFrame:
    spot = _get_spot()
    if spot.empty or "最新份额" not in spot.columns or "_data_date" not in spot.columns:
        return pd.DataFrame(columns=["code", "secondary_shares_raw"])
    rows = spot[spot["_data_date"] == day].copy()
    if rows.empty:
        return pd.DataFrame(columns=["code", "secondary_shares_raw"])
    rows["secondary_shares_raw"] = pd.to_numeric(rows["最新份额"], errors="coerce")
    rows = rows[["代码", "secondary_shares_raw"]].rename(columns={"代码": "code"})
    return rows.dropna(subset=["secondary_shares_raw"]).drop_duplicates("code", keep="last")


def _same_day_spot(day: date) -> pd.DataFrame:
    spot = _get_spot()
    if spot.empty or "_data_date" not in spot.columns:
        return pd.DataFrame()
    return spot[spot["_data_date"] == day].copy()


def guarded_fetch_reference_prices(day: date) -> pd.DataFrame:
    """Use trading prices only when the spot snapshot belongs to ``day``."""
    daily = base.retry("Eastmoney ETF NAV", base.ak.fund_etf_fund_daily_em)
    nav_column = next(
        (c for c in daily.columns if str(c).startswith(day.isoformat()) and "单位净值" in str(c)),
        None,
    )
    if nav_column is None:
        raise ValueError(f"no ETF NAV column for {day.isoformat()}")

    out = daily.iloc[:, [0, 1]].copy()
    out.columns = ["code", "price_name"]
    out["nav"] = pd.to_numeric(daily[nav_column], errors="coerce")
    out["code"] = out["code"].astype(str).str.zfill(6)
    out = out.drop_duplicates("code", keep="last")

    spot = _same_day_spot(day)
    if spot.empty or not {"最新价", "成交量", "成交额"}.issubset(spot.columns):
        out["reference_price"] = out["nav"]
        out["reference_price_type"] = "NAV"
        return out[["code", "price_name", "reference_price", "reference_price_type"]]

    traded = pd.DataFrame({
        "code": spot["代码"].astype(str).str.zfill(6),
        "close": pd.to_numeric(spot["最新价"], errors="coerce"),
        "volume": pd.to_numeric(spot["成交量"], errors="coerce"),
        "amount": pd.to_numeric(spot["成交额"], errors="coerce"),
    }).drop_duplicates("code", keep="last")
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_share = traded["amount"] / traded["volume"]
        raw_lot = traded["amount"] / (traded["volume"] * 100)
    close = traded["close"].where(traded["close"] > 0)
    pick_share = (raw_share / close - 1).abs() < (raw_lot / close - 1).abs()
    avg = raw_share.where(pick_share, raw_lot)
    ratio = avg / close
    avg_ok = avg.notna() & (avg > 0) & ratio.between(0.5, 2.0)
    traded["traded_reference"] = avg.where(avg_ok, close)
    traded["traded_type"] = np.where(avg_ok, "AVG", "CLOSE")

    out = out.merge(traded[["code", "traded_reference", "traded_type"]], on="code", how="left")
    traded_ok = out["traded_reference"].notna() & (out["traded_reference"] > 0)
    nav_ok = out["nav"].notna() & (out["nav"] > 0)
    nav_gap = (out["traded_reference"] / out["nav"] - 1).abs()
    consistent = traded_ok & (~nav_ok | (nav_gap <= PRICE_NAV_MAX_DEVIATION))
    out["reference_price"] = out["traded_reference"].where(consistent, out["nav"])
    out["reference_price_type"] = out["traded_type"].where(consistent, "NAV")

    guarded = out[traded_ok & nav_ok & (nav_gap > PRICE_NAV_MAX_DEVIATION)]
    for row in guarded.itertuples(index=False):
        _PRICE_GUARDS.append({
            "code": str(row.code), "name": str(row.price_name),
            "tradedReference": round(float(row.traded_reference), 4),
            "nav": round(float(row.nav), 4),
            "deviationPct": round(float(abs(row.traded_reference / row.nav - 1) * 100), 2),
        })
    return out[["code", "price_name", "reference_price", "reference_price_type"]]


def _candidate_split_factor(previous_shares: float, current_shares: float) -> float | None:
    if not (previous_shares > 0 and current_shares > 0):
        return None
    ratio = current_shares / previous_shares
    factors = (0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0)
    nearest = min(factors, key=lambda x: abs(ratio / x - 1))
    return nearest if abs(ratio / nearest - 1) <= 0.04 else None


def _confirm_split_by_price(code: str, previous_day: date, current_day: date, factor: float) -> bool:
    try:
        hist = base.retry(
            f"Eastmoney ETF split check {code}",
            lambda: base.ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=previous_day.strftime("%Y%m%d"),
                end_date=current_day.strftime("%Y%m%d"),
                adjust="",
            ),
            attempts=2,
        )
    except Exception:
        return False
    if hist.empty or "日期" not in hist.columns or "收盘" not in hist.columns:
        return False
    frame = hist.copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
    frame["收盘"] = pd.to_numeric(frame["收盘"], errors="coerce")
    frame = frame[frame["日期"].isin([previous_day, current_day])].dropna(subset=["日期", "收盘"])
    if len(frame) < 2:
        return False
    prices = frame.drop_duplicates("日期", keep="last").set_index("日期")["收盘"]
    if previous_day not in prices.index or current_day not in prices.index or prices.loc[previous_day] <= 0:
        return False
    price_ratio = float(prices.loc[current_day] / prices.loc[previous_day])
    expected = 1.0 / factor
    return abs(price_ratio / expected - 1) <= 0.15


def guarded_fetch_share_window(
    end_day: date,
    end_frame: pd.DataFrame,
    sessions: int = base.WINDOW_SESSIONS,
) -> list[tuple[date, pd.DataFrame]]:
    """Restate pre-split history into the current share unit when confirmed."""
    window = _ORIG_FETCH_SHARE_WINDOW(end_day, end_frame, sessions)
    adjusted = [(d, frame.copy()) for d, frame in window]
    if len(adjusted) < 2:
        return adjusted

    for i in range(1, len(adjusted)):
        prev_day, prev = adjusted[i - 1]
        cur_day, cur = adjusted[i]
        prev_map = prev.set_index("code")["shares"]
        cur_map = cur.set_index("code")["shares"]
        common = sorted(set(prev_map.index) & set(cur_map.index))
        for code in common:
            prv = float(prev_map.loc[code])
            now = float(cur_map.loc[code])
            factor = _candidate_split_factor(prv, now)
            if factor is None:
                continue
            if not _confirm_split_by_price(str(code), prev_day, cur_day, factor):
                continue
            for j in range(i):
                frame = adjusted[j][1]
                mask = frame["code"] == code
                if mask.any():
                    frame.loc[mask, "shares"] = pd.to_numeric(frame.loc[mask, "shares"], errors="coerce") * factor
            _CORPORATE_ACTIONS.append({
                "code": str(code),
                "date": cur_day.isoformat(),
                "shareFactor": round(float(factor), 6),
                "method": "share jump + inverse unadjusted price move",
            })
    return adjusted


def _append_issue(snapshot: dict[str, Any], severity: str, check: str, message: str) -> None:
    snapshot.setdefault("quality", {}).setdefault("issues", []).append(
        {"severity": severity, "check": check, "message": message}
    )
    if severity == "critical":
        snapshot["status"] = "failed"
    elif snapshot.get("status") == "verified":
        snapshot["status"] = "warning"


def _apply_flow_sanity_gate(snapshot: dict[str, Any]) -> None:
    flagged: list[dict[str, Any]] = []
    for item in snapshot.get("etfs", []):
        flow = item.get("flow1d")
        aum = item.get("aum")
        if not isinstance(flow, (int, float)) or not isinstance(aum, (int, float)):
            continue
        prior_aum = float(aum) - float(flow)
        ratio = abs(float(flow)) / max(abs(prior_aum), 0.01)
        hard_stop = abs(float(flow)) >= FLOW_HARD_STOP_ABS_BN and ratio >= FLOW_HARD_STOP_PRIOR_AUM_RATIO
        review = abs(float(flow)) >= FLOW_REVIEW_ABS_BN and ratio >= FLOW_REVIEW_PRIOR_AUM_RATIO
        if hard_stop or review:
            flagged.append({
                "code": str(item.get("code")), "name": str(item.get("name")),
                "flow1d": round(float(flow), 2), "priorAumApprox": round(prior_aum, 2),
                "flowToPriorAum": round(ratio, 4),
            })
    if flagged:
        snapshot.setdefault("quality", {})["extremeFlowReview"] = flagged
        names = "、".join(f"{x['name']}({x['code']})" for x in flagged[:5])
        _append_issue(
            snapshot,
            "critical",
            "single_etf_extreme_flow",
            f"单只ETF份额变化对应资金超过安全阈值，已停止发布并要求复核：{names}",
        )


def guarded_build_snapshot(day: date, current: pd.DataFrame | None = None) -> dict[str, Any]:
    global _SHARE_AUDIT
    _reset_run_state()
    current = current if current is not None else base.fetch_exchange_shares(day)

    short_window = _ORIG_FETCH_SHARE_WINDOW(day, current, sessions=2)
    previous = short_window[-2][1]
    secondary = _secondary_for_day(day)
    if secondary.empty:
        _SHARE_AUDIT = {
            "status": "unavailable",
            "reason": _SPOT_ERROR or "same-day Eastmoney 最新份额 unavailable",
            "repaired": [],
        }
        guarded_current = current
    else:
        guarded_current, _SHARE_AUDIT = repair_current_shares(current, previous, secondary)

    snapshot = _ORIG_BUILD_SNAPSHOT(day, guarded_current)
    snapshot.setdefault("quality", {})["shareCrossCheck"] = _SHARE_AUDIT
    snapshot["quality"]["priceNavGuards"] = _PRICE_GUARDS
    snapshot["quality"]["corporateActions"] = _CORPORATE_ACTIONS

    if _SHARE_AUDIT.get("status") == "unusable":
        _append_issue(
            snapshot,
            "warning",
            "secondary_share_crosscheck",
            "东方财富最新份额与交易所横截面无法稳定对齐，本次未使用其修复任何官方份额。",
        )
    if _PRICE_GUARDS:
        _append_issue(
            snapshot,
            "warning",
            "price_nav_guard",
            f"{len(_PRICE_GUARDS)}只ETF成交参考价与同日NAV偏离超过{PRICE_NAV_MAX_DEVIATION:.0%}，已回退NAV。",
        )
    if _CORPORATE_ACTIONS:
        _append_issue(
            snapshot,
            "warning",
            "corporate_action_adjustment",
            f"识别并调整{len(_CORPORATE_ACTIONS)}个经价格反向跳变确认的ETF份额拆分/合并事件。",
        )

    _apply_flow_sanity_gate(snapshot)
    return snapshot


def install_guards() -> None:
    base.fetch_reference_prices = guarded_fetch_reference_prices
    base.fetch_share_window = guarded_fetch_share_window
    base.build_snapshot = guarded_build_snapshot


def main() -> int:
    install_guards()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
