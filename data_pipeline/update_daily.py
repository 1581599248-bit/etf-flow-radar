"""Generate the client-facing A-share ETF daily flow monitor.

Facts and estimates are kept separate:
* ETF shares: official SSE/SZSE end-of-day observations after clearing.
* Reference value: same-day ETF average traded price (amount / volume), falling
  back to the exchange close, then to same-day NAV when trading data is missing.
* Estimated flow: share change multiplied by the latest verified reference value.
* Return: exchange-traded close of the largest ETF in each observation group.

The dataset cannot identify investor identity or intent. The conclusion engine is
deterministic and may describe allocation direction, concentration and price-flow
state, but never attributes activity to the "national team" or another holder.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import akshare as ak
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "site" / "data"
CONFIG = json.loads((Path(__file__).parent / "classification.json").read_text("utf-8"))
EXCLUDE = re.compile("|".join(CONFIG["globalExcludePatterns"]), re.IGNORECASE)
RULES = {kind: CONFIG[kind] for kind in ("broad", "style", "industry", "industryDetail") if kind in CONFIG}
# A product must never disappear from the daily A-share total merely because a
# new index name has not yet been given a more specific label.  This is a
# deliberately transparent holding group, not a claim that the product is a
# broad index, style factor, or a single industry exposure.  It keeps the
# one-day market denominator complete while allowing the taxonomy to evolve
# without silently changing the aggregate flow.
FALLBACK_A_SHARE_RULE = {
    "id": "other_a_share_stock_etf",
    "name": "其他A股股票ETF",
    "code": None,
    "fallback": True,
}
RULES["other"] = [FALLBACK_A_SHARE_RULE]
MIN_MARKET_ETFS = 500
MIN_CLASSIFIED_ETFS = 300
WINDOW_SESSIONS = 21
MAX_UNIVERSE_DROP_RATIO = 0.02


def retry(label: str, operation: Callable[[], pd.DataFrame], attempts: int = 5) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if not isinstance(result, pd.DataFrame):
                raise TypeError(f"{label} returned {type(result).__name__}, expected DataFrame")
            return result
        except Exception as exc:  # upstream network/schema errors vary
            last_error = exc
            if attempt < attempts:
                time.sleep(min(30, 3 * (2 ** (attempt - 1))))
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


def latest_weekday(day: date) -> date:
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def fetch_sse_shares(day: date) -> pd.DataFrame:
    """Call the official SSE endpoint with timeout/backoff-safe parsing.

    AKShare's wrapper is intentionally simple and calls ``response.json()``
    without a timeout. Shared CI exits occasionally receive an empty or HTML
    response, so we keep the exact official query but validate HTTP and JSON.
    """
    url = "https://query.sse.com.cn/commonQuery.do"
    params = {
        "isPagination": "true", "pageHelp.pageSize": "10000", "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1", "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", "STAT_DATE": day.isoformat(),
    }
    headers = {
        "Referer": "https://www.sse.com.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
    }
    response = requests.get(url, params=params, headers=headers, timeout=(10, 30))
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result")
    if not isinstance(result, list):
        raise ValueError("SSE response omitted result rows")
    required = ["NUM", "SEC_CODE", "SEC_NAME", "ETF_TYPE", "STAT_DATE", "TOT_VOL"]
    if not result:
        return pd.DataFrame(columns=["序号", "基金代码", "基金简称", "ETF类型", "统计日期", "基金份额"])
    frame = pd.DataFrame(result)
    if any(column not in frame.columns for column in required):
        raise ValueError("SSE response schema changed")
    frame = frame[required].copy()
    frame.columns = ["序号", "基金代码", "基金简称", "ETF类型", "统计日期", "基金份额"]
    frame["基金份额"] = pd.to_numeric(frame["基金份额"], errors="coerce") * 10000
    return frame


def fetch_exchange_shares(day: date) -> pd.DataFrame:
    """Fetch one exact official day-end share cross-section."""
    stamp = day.strftime("%Y%m%d")
    sse_raw = retry("SSE ETF shares", lambda: fetch_sse_shares(day))
    if sse_raw.empty:
        raise ValueError(f"{day.isoformat()} SSE closing shares have not been published")
    szse_raw = retry(
        "SZSE ETF shares",
        lambda: ak.fund_scale_daily_szse(start_date=stamp, end_date=stamp, symbol="ETF"),
    )
    if szse_raw.empty:
        raise ValueError(f"{day.isoformat()} is not a complete SSE/SZSE trading day")
    if sse_raw.shape[1] < 6 or szse_raw.shape[1] < 4:
        raise ValueError("official exchange response schema changed")

    sse = sse_raw.iloc[:, [1, 2, 4, 5]].copy()
    sse.columns = ["code", "name", "trade_date", "shares"]
    sse["exchange"] = "SSE"
    szse = szse_raw.iloc[:, :4].copy()
    szse.columns = ["trade_date", "code", "name", "shares"]
    szse["exchange"] = "SZSE"
    result = pd.concat([sse, szse], ignore_index=True)
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").dt.strftime("%Y-%m-%d")
    result["shares"] = pd.to_numeric(result["shares"], errors="coerce")
    if set(result["trade_date"].unique()) != {day.isoformat()}:
        raise ValueError("official exchange response date differs from request")
    if result["code"].duplicated().any():
        raise ValueError("official exchange response contains duplicate ETF codes")
    if result[["code", "name", "shares"]].isna().any().any() or (result["shares"] < 0).any():
        raise ValueError("official exchange response contains invalid ETF identifiers or shares")
    return result


def fetch_available_shares(on_or_before: date, lookback_days: int = 12) -> tuple[date, pd.DataFrame]:
    errors: list[str] = []
    for offset in range(lookback_days):
        candidate = on_or_before - timedelta(days=offset)
        if candidate.weekday() > 4:
            continue
        try:
            frame = fetch_exchange_shares(candidate)
            if len(frame) >= MIN_MARKET_ETFS:
                return candidate, frame
            errors.append(f"{candidate}: only {len(frame)} rows")
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("no complete trading day found; " + " | ".join(errors[-3:]))


def fetch_share_window(end_day: date, end_frame: pd.DataFrame, sessions: int = WINDOW_SESSIONS) -> list[tuple[date, pd.DataFrame]]:
    """Return exact official sessions ending at end_day, oldest first."""
    found: list[tuple[date, pd.DataFrame]] = [(end_day, end_frame)]
    candidate = end_day - timedelta(days=1)
    while len(found) < sessions and candidate >= end_day - timedelta(days=52):
        if candidate.weekday() <= 4:
            try:
                frame = fetch_exchange_shares(candidate)
                if len(frame) >= MIN_MARKET_ETFS:
                    found.append((candidate, frame))
                    print(f"official share history: {len(found)}/{sessions} ({candidate})", flush=True)
                    time.sleep(.35)
            except Exception:
                pass  # weekends are skipped above; holidays have empty exchange responses
        candidate -= timedelta(days=1)
    if len(found) < sessions:
        raise RuntimeError(f"only {len(found)} complete official sessions found; need {sessions}")
    return sorted(found, key=lambda item: item[0])


def fetch_full_names() -> pd.DataFrame:
    """Fund-manager-qualified full short names (e.g. 科创半导体设备ETF鹏华).

    This source is best-effort: if it is unreachable (e.g. blocked from CI),
    the pipeline must still publish with shorter official names.
    """
    try:
        frame = retry("Eastmoney fund full names", ak.fund_name_em, attempts=2)
    except Exception as exc:
        print(f"full name source unavailable, falling back to shorter names: {exc}", file=sys.stderr)
        return pd.DataFrame(columns=["code", "full_name"])
    out = frame[["基金代码", "基金简称"]].copy()
    out.columns = ["code", "full_name"]
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["full_name"] = out["full_name"].astype(str).str.strip()
    out = out[out["full_name"].str.len() > 0]
    return out.drop_duplicates("code", keep="last")


def _display_name(row: Any) -> str:
    """Best available display name: full name, else longer NAV name, else exchange name."""
    name = str(row.display_name)
    price_name = getattr(row, "price_name", None)
    if name == str(row.name) and pd.notna(price_name):
        alt = str(price_name).strip()
        if len(alt) > len(name):
            return alt
    return name


def with_display_names(frame: pd.DataFrame, names: pd.DataFrame) -> pd.DataFrame:
    """Attach full names for display; 'name' stays the official exchange short name."""
    merged = frame.merge(names, on="code", how="left", validate="many_to_one")
    merged["display_name"] = merged["full_name"].where(merged["full_name"].notna(), merged["name"])
    return merged.drop(columns=["full_name"])


def fetch_trading_prices() -> pd.DataFrame:
    """Same-day average traded price (amount / volume) from the Eastmoney snapshot.

    The snapshot is taken after the close on the run day, so its amount/volume
    describe that trade day. Eastmoney reports volume in lots (100 shares); the
    lot/share ambiguity is resolved by picking whichever conversion lands closer
    to the close, then the average must stay within a sane band around the close
    or the close itself is used instead.
    """
    spot = retry("Eastmoney ETF spot", ak.fund_etf_spot_em)
    spot.columns = [str(c).strip() for c in spot.columns]
    out = pd.DataFrame({
        "code": spot["代码"].astype(str).str.zfill(6),
        "close": pd.to_numeric(spot["最新价"], errors="coerce"),
        "volume": pd.to_numeric(spot["成交量"], errors="coerce"),
        "amount": pd.to_numeric(spot["成交额"], errors="coerce"),
    })
    out = out.drop_duplicates("code", keep="last")
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_share = out["amount"] / out["volume"]
        raw_lot = out["amount"] / (out["volume"] * 100)
    close = out["close"].where(out["close"] > 0)
    pick_share = (raw_share / close - 1).abs() < (raw_lot / close - 1).abs()
    avg = raw_share.where(pick_share, raw_lot)
    ratio = avg / close
    avg_ok = avg.notna() & (avg > 0) & ratio.between(0.5, 2.0)
    out["reference_price"] = avg.where(avg_ok, close)
    out["reference_price_type"] = np.where(avg_ok, "AVG", "CLOSE")
    return out[["code", "reference_price", "reference_price_type"]]


def fetch_reference_prices(day: date) -> pd.DataFrame:
    """Reference value for flow estimates: same-day average traded price first.

    Same-day NAV is still fetched and required: it validates that observations
    exist for the exact target day (never silently substitute another date),
    supplies the fund full name used for classification, and serves as the
    fallback reference value for ETFs missing from the trading snapshot.
    """
    daily = retry("Eastmoney ETF NAV", ak.fund_etf_fund_daily_em)
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
    try:
        traded = fetch_trading_prices()
    except Exception as exc:  # trading snapshot unreachable: NAV keeps the pipeline alive
        print(f"[warn] trading price snapshot failed ({exc}); falling back to NAV", file=sys.stderr)
        traded = pd.DataFrame(columns=["code", "reference_price", "reference_price_type"])
    out = out.merge(traded, on="code", how="left", validate="one_to_one")
    use_traded = out["reference_price"].notna() & (out["reference_price"] > 0)
    out["reference_price"] = out["reference_price"].where(use_traded, out["nav"])
    out["reference_price_type"] = out["reference_price_type"].where(use_traded, "NAV")
    return out[["code", "price_name", "reference_price", "reference_price_type"]]


def classify_etf(name: str, price_name: str | None = None) -> dict[str, Any] | None:
    """Assign one mutually exclusive primary observation group.

    Classification priority is style, hot sub-industry detail, explicit SW2021
    industry, then broad index. Sub-industry detail groups (e.g. semiconductors
    inside electronics) report kind "industry" and carry a "parent" SW industry
    id. Each ETF enters exactly one primary group; cross-industry themes are
    intentionally excluded from this dashboard.
    """
    combined = " ".join(value for value in (name, price_name) if value)
    if EXCLUDE.search(combined):
        return None
    aliases = [(name, 0)]
    if price_name:
        aliases.append((price_name, len(name) + 1))
    matches: dict[str, tuple[int, dict[str, Any]]] = {}
    for kind in ("style", "industryDetail", "industry", "broad"):
        for rule in RULES[kind]:
            positions = [
                offset + match.start()
                for alias, offset in aliases for pattern in rule["patterns"]
                if (match := re.search(pattern, alias, re.IGNORECASE))
            ]
            if positions and (kind not in matches or min(positions) < matches[kind][0]):
                matches[kind] = (min(positions), rule)
    if "style" in matches:
        return {"kind": "style", **matches["style"][1]}
    if "industryDetail" in matches:
        if "broad" in matches and matches["broad"][0] < matches["industryDetail"][0]:
            return {"kind": "broad", **matches["broad"][1]}
        return {"kind": "industry", **matches["industryDetail"][1]}
    if "industry" in matches and "broad" in matches:
        kind = "broad" if matches["broad"][0] <= matches["industry"][0] else "industry"
        return {"kind": kind, **matches[kind][1]}
    if "industry" in matches:
        return {"kind": "industry", **matches["industry"][1]}
    if "broad" in matches:
        return {"kind": "broad", **matches["broad"][1]}
    # Keep non-excluded products visible instead of treating an unfamiliar
    # domestic index name as an invisible residual.  The asset-scope gate in
    # flow_model_v2 remains authoritative, so bonds, money, commodities and
    # cross-border products still cannot enter client-facing A-share groups.
    return {"kind": "other", **FALLBACK_A_SHARE_RULE}


def audit_universe(current: pd.DataFrame, previous: pd.DataFrame) -> dict[str, Any]:
    """Diff complete official cross-sections so product lifecycle changes are visible."""
    current, previous = current.copy(), previous.copy()
    for frame in (current, previous):
        if "display_name" not in frame.columns:
            frame["display_name"] = frame["name"]
    current_rows = current.set_index("code", drop=False)
    previous_rows = previous.set_index("code", drop=False)
    current_codes, previous_codes = set(current_rows.index), set(previous_rows.index)
    added_codes = sorted(current_codes - previous_codes)
    missing_codes = sorted(previous_codes - current_codes)
    renamed_codes = sorted(
        code for code in current_codes & previous_codes
        if str(current_rows.loc[code, "display_name"]) != str(previous_rows.loc[code, "display_name"])
    )
    product = lambda row: {
        "code": str(row["code"]), "name": str(row["display_name"]), "exchange": str(row["exchange"]),
    }
    exchange_counts = lambda frame: {
        exchange: int((frame["exchange"] == exchange).sum()) for exchange in ("SSE", "SZSE")
    }
    return {
        "currentCount": int(len(current)), "previousCount": int(len(previous)),
        "countChange": int(len(current) - len(previous)),
        "currentExchangeCounts": exchange_counts(current),
        "previousExchangeCounts": exchange_counts(previous),
        "added": [product(current_rows.loc[code]) for code in added_codes],
        "missing": [product(previous_rows.loc[code]) for code in missing_codes],
        "renamed": [{
            "code": code, "exchange": str(row["code"]), "exchange": str(row["exchange"]),
            "previousName": str(previous_rows.loc[code, "display_name"]),
            "currentName": str(current_rows.loc[code, "display_name"]),
        } for code in renamed_codes],
    }
