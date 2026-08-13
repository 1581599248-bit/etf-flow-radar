"""Generate the client-facing A-share ETF daily flow monitor.

Facts and estimates are kept separate:
* ETF shares: official SSE/SZSE end-of-day observations after clearing.
* Reference value: same-day ETF NAV (close only when NAV is unavailable).
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
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "site" / "data"
CONFIG = json.loads((Path(__file__).parent / "classification.json").read_text("utf-8"))
EXCLUDE = re.compile("|".join(CONFIG["globalExcludePatterns"]), re.IGNORECASE)
RULES = {kind: CONFIG[kind] for kind in ("broad", "style", "industry")}
FOCUS_RULES = CONFIG.get("focusEtfFamilies", [])
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


def fetch_reference_prices(day: date) -> pd.DataFrame:
    """Use exact target-day NAV; never silently substitute another date."""
    daily = retry("Eastmoney ETF NAV", ak.fund_etf_fund_daily_em)
    nav_column = next(
        (c for c in daily.columns if str(c).startswith(day.isoformat()) and "单位净值" in str(c)),
        None,
    )
    if nav_column is None:
        raise ValueError(f"no ETF NAV column for {day.isoformat()}")
    out = daily.iloc[:, [0, 1]].copy()
    out.columns = ["code", "price_name"]
    out["reference_price"] = pd.to_numeric(daily[nav_column], errors="coerce")
    out["reference_price_type"] = "NAV"
    out["code"] = out["code"].astype(str).str.zfill(6)
    return out.drop_duplicates("code", keep="last")


def classify_etf(name: str, price_name: str | None = None) -> dict[str, Any] | None:
    """Assign one mutually exclusive primary observation group.

    Classification priority is style, explicit SW2021 industry, then broad
    index. Each ETF enters exactly one primary group; cross-industry themes are
    intentionally excluded from this dashboard.
    """
    combined = " ".join(value for value in (name, price_name) if value)
    if EXCLUDE.search(combined):
        return None
    aliases = [(name, 0)]
    if price_name:
        aliases.append((price_name, len(name) + 1))
    matches: dict[str, tuple[int, dict[str, Any]]] = {}
    for kind in ("style", "industry", "broad"):
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
    if "industry" in matches and "broad" in matches:
        kind = "broad" if matches["broad"][0] <= matches["industry"][0] else "industry"
        return {"kind": kind, **matches[kind][1]}
    if "industry" in matches:
        return {"kind": "industry", **matches["industry"][1]}
    if "broad" in matches:
        return {"kind": "broad", **matches["broad"][1]}
    return None


def focus_family(name: str, price_name: str | None = None) -> dict[str, str] | None:
    """Return a transparent display family for individually listed focus ETFs.

    This is a discovery view, not another aggregation taxonomy. A focus ETF may
    also have a broad/style/SW industry group, but it is never added twice to
    market or industry totals.
    """
    combined = " ".join(value for value in (name, price_name) if value)
    if EXCLUDE.search(combined):
        return None
    for rule in FOCUS_RULES:
        if any(re.search(pattern, combined, re.IGNORECASE) for pattern in rule["patterns"]):
            return {"id": str(rule["id"]), "name": str(rule["name"])}
    return None


def audit_universe(current: pd.DataFrame, previous: pd.DataFrame) -> dict[str, Any]:
    """Diff complete official cross-sections so product lifecycle changes are visible."""
    current_rows = current.set_index("code", drop=False)
    previous_rows = previous.set_index("code", drop=False)
    current_codes, previous_codes = set(current_rows.index), set(previous_rows.index)
    added_codes = sorted(current_codes - previous_codes)
    missing_codes = sorted(previous_codes - current_codes)
    renamed_codes = sorted(
        code for code in current_codes & previous_codes
        if str(current_rows.loc[code, "name"]) != str(previous_rows.loc[code, "name"])
    )
    product = lambda row: {
        "code": str(row["code"]), "name": str(row["name"]), "exchange": str(row["exchange"]),
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
            "code": code, "exchange": str(current_rows.loc[code, "exchange"]),
            "previousName": str(previous_rows.loc[code, "name"]),
            "currentName": str(current_rows.loc[code, "name"]),
        } for code in renamed_codes],
    }


def complete_universe_records(
    current: pd.DataFrame, prices: pd.DataFrame, valid_codes: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Publish every exchange ETF; keep analysis readiness separate from existence."""
    rows = current.merge(prices, on="code", how="left", validate="one_to_one")
    records: list[dict[str, Any]] = []
    unclassified: list[dict[str, str]] = []
    for row in rows.itertuples(index=False):
        group = classify_etf(str(row.name), str(row.price_name) if pd.notna(row.price_name) else None)
        scope = "excluded" if EXCLUDE.search(" ".join(filter(None, [str(row.name), str(row.price_name) if pd.notna(row.price_name) else ""]))) else ("classified" if group else "unclassified")
        if scope == "unclassified":
            unclassified.append({"code": str(row.code), "name": str(row.name), "exchange": str(row.exchange)})
        record = {
            "code": str(row.code), "name": str(row.name), "exchange": str(row.exchange),
            "shares": round(float(row.shares), 2), "classificationStatus": scope,
            "analysisStatus": "ready" if str(row.code) in valid_codes else "history_or_nav_pending",
        }
        if pd.notna(row.price_name):
            record["fullName"] = str(row.price_name)
        if group:
            record.update({"groupId": str(group["id"]), "groupName": str(group["name"]), "kind": str(group["kind"])})
        if pd.notna(row.reference_price):
            record.update({"referencePrice": round(float(row.reference_price), 4), "referencePriceType": str(row.reference_price_type)})
        records.append(record)
    return sorted(records, key=lambda x: (x["exchange"], x["code"])), unclassified


def identify_index(name: str) -> tuple[str, dict[str, Any]] | None:
    """Compatibility helper used by tests and audit notebooks."""
    item = classify_etf(name)
    if not item or item["kind"] != "broad":
        return None
    return str(item["code"]), {"name": item["name"], "group": item["kind"]}


def is_plain_benchmark(name: str) -> bool:
    item = classify_etf(name)
    return bool(item and item["kind"] == "broad")


def percentile(values: list[float], current: float, minimum: int = 60) -> float | None:
    valid = [x for x in values if isinstance(x, (int, float)) and math.isfinite(x)]
    if len(valid) < minimum:
        return None
    return 100 * sum(x <= current for x in valid) / len(valid)


def _sina_symbol(code: str, exchange: str) -> str:
    return ("sh" if exchange == "SSE" else "sz") + code


def fetch_return_series(representatives: list[dict[str, str]], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Fetch one clean liquid proxy per group; keep the JS decoder single-threaded.

    The candidates are ordered by AUM. A series containing a >25% one-session
    discontinuity is rejected as a likely split/adjustment artefact and the next
    ETF in the same group is tried.
    """
    def one(rep: dict[str, str]) -> tuple[str, pd.DataFrame]:
        symbol = _sina_symbol(rep["code"], rep["exchange"])
        raw = retry(f"Sina ETF history {symbol}", lambda: ak.fund_etf_hist_sina(symbol=symbol), attempts=2)
        if raw.empty or not {"date", "close"}.issubset(raw.columns):
            raise ValueError(f"{symbol} price history unavailable")
        raw = raw.copy()
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce").dt.date
        raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
        raw = raw[(raw["date"] >= start) & (raw["date"] <= end)].dropna(subset=["date", "close"])
        return rep["group_id"], raw

    output: dict[str, pd.DataFrame] = {}
    attempted_groups: set[str] = set()
    done_groups = 0
    total_groups = len({rep["group_id"] for rep in representatives})
    for rep in representatives:
        if rep["group_id"] in output:
            continue
        first_attempt = rep["group_id"] not in attempted_groups
        attempted_groups.add(rep["group_id"])
        try:
            key, frame = one(rep)
            if len(frame) < WINDOW_SESSIONS or frame.sort_values("date")["close"].pct_change().abs().max() > .25:
                raise ValueError("unadjusted price discontinuity or insufficient history")
            frame.attrs["code"] = rep["code"]
            frame.attrs["name"] = rep["name"]
            output[key] = frame
            done_groups += 1
        except Exception as exc:
            print(f"price history warning {rep['code']}: {exc}", file=sys.stderr)
        if first_attempt or rep["group_id"] in output:
            print(f"return proxy history: {done_groups}/{total_groups} groups", flush=True)
    return output


def _pct_return(frame: pd.DataFrame | None, sessions: int) -> float | None:
    if frame is None or len(frame) <= sessions:
        return None
    values = frame.sort_values("date")["close"].tolist()
    start = float(values[-sessions - 1])
    return round((float(values[-1]) / start - 1) * 100, 2) if start else None


def _flow_state(ret: float | None, intensity: float | None) -> str:
    if ret is None or intensity is None:
        return "待补充"
    if ret >= 0 and intensity >= 0:
        return "跑赢且流入"
    if ret < 0 <= intensity:
        return "跑输但流入"
    if ret >= 0 > intensity:
        return "跑赢但流出"
    return "跑输且流出"


def _direction(value: float) -> str:
    if value > 0.05:
        return "净流入"
    if value < -0.05:
        return "净流出"
    return "基本持平"


def generate_conclusion(groups: list[dict[str, Any]], market: dict[str, Any], history_ok: bool) -> dict[str, Any]:
    broad = [g for g in groups if g["kind"] == "broad"]
    styles = [g for g in groups if g["kind"] == "style"]
    sectors = [g for g in groups if g["kind"] == "industry"]
    broad_out = sorted(broad, key=lambda g: g["flow1d"])
    sec_in = sorted(sectors, key=lambda g: g["flow1d"], reverse=True)
    sec_out = sorted(sectors, key=lambda g: g["flow1d"])
    sustained_in = sorted(
        [g for g in groups if g["flow1d"] > 0 and g["flow5d"] > 0],
        key=lambda g: g["flow5d"], reverse=True,
    )
    market_word = _direction(market["flow1d"])
    broad_in_count = sum(g["flow1d"] > 0 for g in broad)
    broad_out_count = sum(g["flow1d"] < 0 for g in broad)
    positive_sectors = [g for g in sec_in if g["flow1d"] > 0]
    style_in = max(styles, key=lambda g: g["flow1d"])
    style_out = min(styles, key=lambda g: g["flow1d"])
    sector_headline = (
        f"申万一级行业资金流入居前的是{sec_in[0]['name']}，流出最多的是{sec_out[0]['name']}。"
        if sec_in[0]["flow1d"] > 0
        else f"申万一级行业组当日均未录得净流入，流出最多的是{sec_out[0]['name']}。"
    )
    headline = (
        f"本期可纳入互斥分组的{market['etfCount']}只A股股票ETF当日合计{market_word}{abs(market['flow1d']):.1f}亿元；"
        f"净流入{market['increaseEtfCount1d']}只、净流出{market['decreaseEtfCount1d']}只。"
        f"宽基中{broad_out_count}个流出、{broad_in_count}个流入；{sector_headline}"
    )
    broad_line = (
        f"宽基流出前三为{broad_out[0]['name']}{broad_out[0]['flow1d']:.1f}亿、"
        f"{broad_out[1]['name']}{broad_out[1]['flow1d']:.1f}亿、{broad_out[2]['name']}{broad_out[2]['flow1d']:.1f}亿；"
        f"5日流出最大仍是{min(broad,key=lambda g:g['flow5d'])['name']}。"
    )
    if positive_sectors:
        sector_line = "申万一级行业净流入居前为" + "、".join(
            f"{g['name']}{g['flow1d']:+.1f}亿" for g in positive_sectors[:2]
        ) + f"；净流出最多为{sec_out[0]['name']}{sec_out[0]['flow1d']:+.1f}亿。"
    else:
        sector_line = f"申万一级行业组当日均未录得净流入；流出最多为{sec_out[0]['name']}{sec_out[0]['flow1d']:+.1f}亿。"
    sustained_text = (
        f"{sustained_in[0]['name']}同时录得1日和5日净流入，可继续观察资金延续性。"
        if sustained_in else "目前没有观察组同时录得1日和5日净流入，尚未形成连续流入方向。"
    )
    watch = (
        f"从份额数据看，宽基当日{broad_out_count}/{len(broad)}个组净流出；"
        f"风格组中{style_in['name']}当日变化相对靠前，{style_out['name']}流出较多。"
        f"{sustained_text}"
    )
    anomaly = (
        f"单只ETF大额变化：{market['topInflowEtf']['name']}净流入{market['topInflowEtf']['flow1d']:+.1f}亿元；"
        f"{market['topOutflowEtf']['name']}净流出{market['topOutflowEtf']['flow1d']:+.1f}亿元。"
    )
    return {
        "headline": headline,
        "facts": [broad_line, sector_line, anomaly],
        "interpretation": watch,
        "confidence": "A" if history_ok else "B",
        "confidenceNote": "21个交易日份额完整，价格代理覆盖充分" if history_ok else "历史或价格代理仍有缺口，结论已降级",
    }


def build_snapshot(day: date, current: pd.DataFrame | None = None) -> dict[str, Any]:
    current = current if current is not None else fetch_exchange_shares(day)
    window = fetch_share_window(day, current)
    prices = fetch_reference_prices(day)

    base = current.merge(prices, on="code", how="left", validate="one_to_one")
    classified: list[dict[str, Any]] = []
    focus_candidates: list[dict[str, Any]] = []
    for row in base.itertuples(index=False):
        full_name = str(row.price_name) if pd.notna(row.price_name) else None
        group = classify_etf(str(row.name), full_name)
        family = focus_family(str(row.name), full_name)
        common = {
            "code": str(row.code), "name": str(row.name), "full_name": full_name,
            "exchange": str(row.exchange), "shares": float(row.shares),
            "reference_price": float(row.reference_price) if pd.notna(row.reference_price) else math.nan,
            "reference_price_type": str(row.reference_price_type) if pd.notna(row.reference_price_type) else "",
        }
        if group and pd.notna(row.reference_price):
            classified.append({**common, "group_id": str(group["id"]),
                "group_name": str(group["name"]), "kind": str(group["kind"]),
            })
        if family and pd.notna(row.reference_price):
            focus_candidates.append({
                **common, "family_id": family["id"], "family_name": family["name"],
                "primary_group_id": str(group["id"]) if group else None,
                "primary_group_name": str(group["name"]) if group else None,
                "primary_kind": str(group["kind"]) if group else None,
            })
    etf = pd.DataFrame(classified)
    if etf.empty:
        raise RuntimeError("classification produced no A-share equity ETFs")

    dates = [d for d, _ in window]
    shares_by_date = {d: f.set_index("code")["shares"] for d, f in window}
    def attach_flow_columns(frame: pd.DataFrame) -> pd.DataFrame:
        for offset, label in ((1, "1d"), (5, "5d"), (20, "20d")):
            start_date = dates[-offset - 1]
            frame[f"shares_{label}"] = frame["code"].map(shares_by_date[start_date])
            frame[f"delta_{label}"] = frame["shares"] - frame[f"shares_{label}"]
            frame[f"flow_{label}"] = frame[f"delta_{label}"] * frame["reference_price"] / 1e8
        frame["aum"] = frame["shares"] * frame["reference_price"] / 1e8
        frame["prior_aum_5d"] = frame["shares_5d"] * frame["reference_price"] / 1e8
        frame["prior_aum_20d"] = frame["shares_20d"] * frame["reference_price"] / 1e8
        return frame

    etf = attach_flow_columns(etf)
    focus_etf = attach_flow_columns(pd.DataFrame(focus_candidates)) if focus_candidates else pd.DataFrame()
    valid = etf.dropna(subset=["shares_1d", "shares_5d", "shares_20d"])
    focus_valid = focus_etf.dropna(subset=["shares_1d", "shares_5d", "shares_20d"]) if not focus_etf.empty else focus_etf
    previous = window[-2][1]
    universe_audit = audit_universe(current, previous)
    ready_codes = set(valid["code"].astype(str)) | set(focus_valid["code"].astype(str))
    universe_records, unclassified = complete_universe_records(current, prices, ready_codes)

    reps: list[dict[str, str]] = []
    for group_id, frame in valid.groupby("group_id"):
        for row in frame.nlargest(3, "aum").itertuples(index=False):
            reps.append({"group_id": group_id, "code": str(row.code), "name": str(row.name), "exchange": str(row.exchange)})
    return_series = fetch_return_series(reps, dates[0] - timedelta(days=7), day)
    benchmark = return_series.get("hs300")
    benchmark_20d = _pct_return(benchmark, 20)

    groups: list[dict[str, Any]] = []
    for group_id, frame in valid.groupby("group_id"):
        rule = next(r for kind in RULES.values() for r in kind if r["id"] == group_id)
        kind = str(frame.iloc[0]["kind"])
        aum = float(frame["aum"].sum())
        gross = float(frame["flow_1d"].abs().sum())
        dominant = frame.loc[frame["flow_1d"].abs().idxmax()]
        proxy = frame.loc[frame["aum"].idxmax()]
        rframe = return_series.get(group_id)
        ret1 = _pct_return(rframe, 1)
        ret5 = _pct_return(rframe, 5)
        ret20 = _pct_return(rframe, 20)
        flow1 = float(frame["flow_1d"].sum())
        flow5 = float(frame["flow_5d"].sum())
        flow20 = float(frame["flow_20d"].sum())
        prior5 = float(frame["prior_aum_5d"].sum())
        prior20 = float(frame["prior_aum_20d"].sum())
        breadth = lambda col: float(((frame[col] > 0).sum() - (frame[col] < 0).sum()) / len(frame) * 100)
        counts = lambda col: {
            "increase": int((frame[col] > 0).sum()),
            "decrease": int((frame[col] < 0).sum()),
            "unchanged": int((frame[col] == 0).sum()),
        }
        count1, count5 = counts("delta_1d"), counts("delta_5d")
        intensity1 = flow1 / max(aum - flow1, .01) * 100
        intensity5 = flow5 / max(prior5, .01) * 100
        intensity20 = flow20 / max(prior20, .01) * 100
        relative20 = round(ret20 - benchmark_20d, 2) if ret20 is not None and benchmark_20d is not None else None
        groups.append({
            "id": group_id, "code": rule.get("code"), "name": rule["name"], "kind": kind,
            "flow1d": round(flow1, 2), "flow5d": round(flow5, 2), "flow20d": round(flow20, 2),
            "flowIntensity1dPct": round(intensity1, 2),
            "flowIntensity5dPct": round(intensity5, 2),
            "flowIntensity20dPct": round(intensity20, 2),
            "flowIntensity1dBps": round(intensity1 * 100, 1),
            "flowIntensity5dBps": round(intensity5 * 100, 1),
            "flowIntensity20dBps": round(intensity20 * 100, 1),
            "return1d": ret1, "return5d": ret5, "return20d": ret20,
            "relativeReturn20d": relative20,
            "priceFlowState": _flow_state(relative20, intensity5),
            "breadth1d": round(breadth("delta_1d"), 1), "breadth5d": round(breadth("delta_5d"), 1),
            "increaseEtfCount1d": count1["increase"], "decreaseEtfCount1d": count1["decrease"],
            "unchangedEtfCount1d": count1["unchanged"], "increaseEtfCount5d": count5["increase"],
            "decreaseEtfCount5d": count5["decrease"], "unchangedEtfCount5d": count5["unchanged"],
            "aum": round(aum, 2), "etfCount": int(len(frame)),
            "concentration1d": round(abs(float(dominant["flow_1d"])) / gross * 100, 1) if gross else 0,
            "representative": {
                "code": str(rframe.attrs.get("code", proxy["code"])) if rframe is not None else str(proxy["code"]),
                "name": str(rframe.attrs.get("name", proxy["name"])) if rframe is not None else str(proxy["name"]),
            },
            "dominantEtf": {"code": str(dominant["code"]), "name": str(dominant["name"]), "flow1d": round(float(dominant["flow_1d"]), 2)},
        })

    market_count1 = {
        "increase": int((valid["delta_1d"] > 0).sum()),
        "decrease": int((valid["delta_1d"] < 0).sum()),
        "unchanged": int((valid["delta_1d"] == 0).sum()),
    }
    top_inflow_etf = valid.loc[valid["flow_1d"].idxmax()]
    top_outflow_etf = valid.loc[valid["flow_1d"].idxmin()]
    market = {
        "name": "A股股票ETF统计范围", "etfCount": int(len(valid)),
        "flow1d": round(float(valid["flow_1d"].sum()), 2),
        "flow5d": round(float(valid["flow_5d"].sum()), 2),
        "flow20d": round(float(valid["flow_20d"].sum()), 2),
        "aum": round(float(valid["aum"].sum()), 2),
        "breadth1d": round(float(((valid["delta_1d"] > 0).sum() - (valid["delta_1d"] < 0).sum()) / len(valid) * 100), 1),
        "increaseEtfCount1d": market_count1["increase"],
        "decreaseEtfCount1d": market_count1["decrease"],
        "unchangedEtfCount1d": market_count1["unchanged"],
        "unchangedEtfPct1d": round(market_count1["unchanged"] / len(valid) * 100, 2),
        "topInflowEtf": {"code": str(top_inflow_etf["code"]), "name": str(top_inflow_etf["name"]), "flow1d": round(float(top_inflow_etf["flow_1d"]), 2)},
        "topOutflowEtf": {"code": str(top_outflow_etf["code"]), "name": str(top_outflow_etf["name"]), "flow1d": round(float(top_outflow_etf["flow_1d"]), 2)},
    }
    expected_groups = len({rep["group_id"] for rep in reps})
    price_coverage = len(return_series) / max(expected_groups, 1)
    issues: list[dict[str, str]] = []
    if len(current) < MIN_MARKET_ETFS:
        issues.append({"severity": "critical", "check": "market_coverage", "message": f"全市场ETF仅{len(current)}只"})
    if len(current) < len(previous) * (1 - MAX_UNIVERSE_DROP_RATIO):
        issues.append({"severity": "critical", "check": "universe_drop", "message": f"交易所ETF总数较前一交易日下降超过{MAX_UNIVERSE_DROP_RATIO:.0%}"})
    for exchange in ("SSE", "SZSE"):
        current_count = universe_audit["currentExchangeCounts"][exchange]
        previous_count = universe_audit["previousExchangeCounts"][exchange]
        if current_count < previous_count * (1 - MAX_UNIVERSE_DROP_RATIO):
            issues.append({"severity": "critical", "check": f"{exchange.lower()}_universe_drop", "message": f"{exchange} ETF数量较前一交易日下降超过{MAX_UNIVERSE_DROP_RATIO:.0%}"})
    if len(valid) < MIN_CLASSIFIED_ETFS:
        issues.append({"severity": "critical", "check": "classification_coverage", "message": f"可识别A股股票ETF仅{len(valid)}只"})
    if price_coverage < .8:
        issues.append({"severity": "warning", "check": "return_proxy_coverage", "message": f"组别收益代理覆盖率{price_coverage:.1%}"})
    count_total = sum(market_count1.values())
    group_etf_total = sum(g["etfCount"] for g in groups)
    unique_etf_total = int(valid["code"].nunique())
    group_flow_1d = round(sum(g["flow1d"] for g in groups), 2)
    flow_reconciliation_diff = round(group_flow_1d - market["flow1d"], 2)
    if count_total != len(valid):
        issues.append({"severity": "critical", "check": "direction_count_reconciliation", "message": "流入、流出与总份额不变只数无法与统计ETF总数对账"})
    if group_etf_total != len(valid) or unique_etf_total != len(valid):
        issues.append({"severity": "critical", "check": "group_count_reconciliation", "message": "观察组ETF数量存在遗漏或重复"})
    if abs(flow_reconciliation_diff) > 0.5:
        issues.append({"severity": "critical", "check": "flow_reconciliation", "message": "观察组资金变化合计与全体ETF合计偏差超过舍入容差"})
    critical = any(i["severity"] == "critical" for i in issues)
    history_ok = len(window) == WINDOW_SESSIONS and price_coverage >= .8
    groups = sorted(groups, key=lambda x: (x["kind"], -x["flow1d"]))
    industry_groups = [g for g in groups if g["kind"] == "industry"]
    industry_etf_count = sum(g["etfCount"] for g in industry_groups)
    industry_group_ids = {g["id"] for g in industry_groups}
    industry_missing_groups = [
        {"id": rule["id"], "code": rule["code"], "name": rule["name"]}
        for rule in RULES["industry"] if rule["id"] not in industry_group_ids
    ]
    industry_universe_count = sum(
        record.get("classificationStatus") == "classified" and record.get("kind") == "industry"
        for record in universe_records
    )
    conclusion = generate_conclusion(groups, market, history_ok)

    records = [{
        "code": str(r.code), "name": str(r.name), "exchange": str(r.exchange),
        **({"fullName": str(r.full_name)} if r.full_name else {}),
        "groupId": str(r.group_id), "groupName": str(r.group_name), "kind": str(r.kind),
        "aum": round(float(r.aum), 2), "flow1d": round(float(r.flow_1d), 2),
        "flow5d": round(float(r.flow_5d), 2), "flow20d": round(float(r.flow_20d), 2),
        "referencePrice": round(float(r.reference_price), 4), "referencePriceType": str(r.reference_price_type),
    } for r in valid.itertuples(index=False)]
    focus_records = [{
        "code": str(r.code), "name": str(r.name), "exchange": str(r.exchange),
        **({"fullName": str(r.full_name)} if r.full_name else {}),
        "familyId": str(r.family_id), "familyName": str(r.family_name),
        "primaryGroupId": str(r.primary_group_id) if r.primary_group_id else None,
        "primaryGroupName": str(r.primary_group_name) if r.primary_group_name else None,
        "primaryKind": str(r.primary_kind) if r.primary_kind else None,
        "aum": round(float(r.aum), 2), "flow1d": round(float(r.flow_1d), 2),
        "flow5d": round(float(r.flow_5d), 2), "flow20d": round(float(r.flow_20d), 2),
        "referencePrice": round(float(r.reference_price), 4),
        "referencePriceType": str(r.reference_price_type),
    } for r in focus_valid.itertuples(index=False)]

    generated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    return {
        "schemaVersion": 6, "status": "failed" if critical else ("warning" if issues else "verified"),
        "tradeDate": day.isoformat(), "previousTradeDate": dates[-2].isoformat(),
        "windowStartDate": dates[0].isoformat(), "generatedAt": generated_at.isoformat(timespec="seconds"),
        "publicationDate": generated_at.date().isoformat(),
        "sourceMode": "REAL", "market": market, "conclusion": conclusion, "groups": groups,
        "etfs": sorted(records, key=lambda x: abs(x["flow1d"]), reverse=True),
        "focusEtfs": sorted(focus_records, key=lambda x: abs(x["flow1d"]), reverse=True),
        "universe": universe_records,
        "universeAudit": {**universe_audit, "unclassifiedCount": len(unclassified), "unclassified": unclassified},
        "quality": {"marketEtfCount": int(len(current)), "classifiedEtfCount": int(len(valid)),
                    "completeUniverseCount": len(universe_records), "unclassifiedEtfCount": len(unclassified),
                    "focusEtfCount": len(focus_records),
                    "officialSessions": len(window), "groupCount": len(groups),
                    "industryDefinitionCount": len(RULES["industry"]),
                    "industryGroupCount": len(industry_groups),
                    "industryMissingGroups": industry_missing_groups,
                    "industryEtfCount": industry_etf_count,
                    "industryUniverseCount": industry_universe_count,
                    "industryPendingCount": industry_universe_count - industry_etf_count,
                    "returnProxyCoverage": round(price_coverage, 4),
                    "reconciliation": {"directionCountTotal": count_total, "groupEtfCountTotal": group_etf_total,
                                       "uniqueAnalyzedEtfCount": unique_etf_total, "groupFlow1d": group_flow_1d,
                                       "marketFlow1d": market["flow1d"], "flowDifference": flow_reconciliation_diff},
                    "issues": issues},
        "sources": [
            {"name": "上海证券交易所", "field": "沪市ETF日终总份额", "role": "官方主源"},
            {"name": "深圳证券交易所", "field": "深市ETF日终总份额", "role": "官方主源"},
            {"name": "东方财富基金净值/AKShare", "field": f"{day.isoformat()} 单位净值", "role": "份额变动估值"},
            {"name": "新浪行情/AKShare", "field": "组内最大规模ETF收盘价", "role": "1/5/20日收益代理"},
        ],
        "methodology": {
            "flow": "参考净申赎 =（期末份额 − 期初份额）× 期末已验证单位净值；金额用于方向与量级观察，不等同基金公司的最终现金流。",
            "counts": "ETF只数按交易所日终总份额较前一交易日增加、减少或完全相同划分。总份额不变表示当日没有净份额增减，不代表没有二级市场成交、价格波动，亦不代表申购和赎回均为零。",
            "return": "组别收益使用组内当前规模最大的ETF作为价格代理；相对收益以沪深300代理为基准。",
            "coordinates": "横轴 = 20日相对沪深300收益率；纵轴 = 5日净申赎 ÷ 5日前参考规模（%）；气泡面积 = 当前ETF规模。",
            "classification": f"SW2021_L1_ETF_V3：行业名称与代码采用申万行业分类标准2021版31个一级行业；只将名称能够高置信对应单一一级行业的ETF纳入行业汇总。白酒、酒类归食品饮料；机器人、泛消费、AI等跨行业指数不强行归类，改在重点ETF异动中逐只展示且不参与行业合计。当前有{len(industry_groups)}个行业实际存在可分析ETF。",
            "identity": "份额数据不包含投资者身份，禁止据此推断国家队、机构、个人或做市商。",
            "scope": "完整名册保留交易所全部ETF；主分组资金分析只使用已明确归类且具备完整历史与净值的A股股票ETF，每只ETF只进入一个主要组。跨行业与热门ETF清单同样要求完整历史与净值，但仅逐只展示，不参与分组资金合计。",
        },
    }


def atomic_publish(snapshot: dict[str, Any]) -> Path:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    target = PUBLIC / "latest.json"
    if snapshot["status"] == "failed":
        failure = PUBLIC / "last-failure.json"
        failure.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), "utf-8")
        raise RuntimeError("quality gate failed; verified snapshot was not replaced")
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(target)
    archive = PUBLIC / "history" / f'{snapshot["tradeDate"]}.json'
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(target.read_text("utf-8"), "utf-8")
    universe_dir = PUBLIC / "universe"
    universe_dir.mkdir(parents=True, exist_ok=True)
    universe_payload = {
        "schemaVersion": snapshot["schemaVersion"], "tradeDate": snapshot["tradeDate"],
        "generatedAt": snapshot["generatedAt"], "universe": snapshot["universe"],
        "audit": snapshot["universeAudit"],
    }
    (universe_dir / "latest.json").write_text(json.dumps(universe_payload, ensure_ascii=False, indent=2), "utf-8")
    (universe_dir / f'{snapshot["tradeDate"]}.json').write_text(json.dumps(universe_payload, ensure_ascii=False, indent=2), "utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to the latest complete official session available today")
    args = parser.parse_args()
    try:
        if args.date:
            day = date.fromisoformat(args.date)
            current = fetch_exchange_shares(day)
        else:
            day, current = fetch_available_shares(latest_weekday(date.today()))
            existing = PUBLIC / "latest.json"
            if existing.exists():
                published = json.loads(existing.read_text("utf-8")).get("tradeDate")
                if published == day.isoformat():
                    print(f"no new complete official session: {published}")
                    return 0
        snapshot = build_snapshot(day, current)
        path = atomic_publish(snapshot)
    except Exception as exc:
        print(f"UPDATE FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"verified snapshot: {path} ({snapshot['tradeDate']}, {len(snapshot['etfs'])} classified ETFs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

