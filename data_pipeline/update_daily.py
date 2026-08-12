"""Build a verified A-share ETF flow snapshot from public market data.

The job never fabricates, forward-fills, or mixes trading dates. AKShare is a
pinned collection adapter. SSE/SZSE day-end shares are the authoritative share
source; same-day fund NAV is the preferred reference price.
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

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public" / "data"
MAPPING = json.loads((Path(__file__).parent / "index_mapping.json").read_text("utf-8"))


def retry(label: str, operation: Callable[[], pd.DataFrame], attempts: int = 3) -> pd.DataFrame:
    """Retry transient upstream failures without accepting malformed output."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if result is None or not isinstance(result, pd.DataFrame):
                raise TypeError(f"{label} returned an invalid response type")
            return result
        except Exception as exc:  # upstream network and schema errors vary
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


def latest_weekday(day: date) -> date:
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def fetch_exchange_shares(day: date) -> pd.DataFrame:
    """Fetch official day-end shares for one exact trading date."""
    d = day.strftime("%Y%m%d")
    sse_raw = retry("SSE ETF shares", lambda: ak.fund_etf_scale_sse(date=d))
    szse_raw = retry(
        "SZSE ETF shares",
        lambda: ak.fund_scale_daily_szse(start_date=d, end_date=d, symbol="ETF"),
    )
    if sse_raw.empty or szse_raw.empty:
        raise ValueError(f"{day.isoformat()} has no complete SSE/SZSE ETF share observations")
    if sse_raw.shape[1] < 6 or szse_raw.shape[1] < 4:
        raise ValueError("exchange response schema changed")

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
        raise ValueError("exchange response date does not match requested trade date")
    return result


def fetch_available_shares(on_or_before: date, lookback_days: int = 12) -> tuple[date, pd.DataFrame]:
    """Resolve weekends and exchange holidays from observed official data."""
    errors: list[str] = []
    for offset in range(lookback_days):
        candidate = on_or_before - timedelta(days=offset)
        if candidate.weekday() > 4:
            continue
        try:
            frame = fetch_exchange_shares(candidate)
            if len(frame) >= 500:
                return candidate, frame
            errors.append(f"{candidate}: only {len(frame)} rows")
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("no complete trading day found; " + " | ".join(errors[-3:]))


def fetch_reference_prices(day: date) -> pd.DataFrame:
    """Get target-day NAV, with same-day close as a strictly dated fallback."""
    daily = retry("Eastmoney ETF NAV", ak.fund_etf_fund_daily_em)
    nav_column = next(
        (column for column in daily.columns if str(column).startswith(day.isoformat()) and "单位净值" in str(column)),
        None,
    )
    if nav_column is None:
        raise ValueError(f"no ETF NAV column for {day.isoformat()}; refusing to use another date")
    nav = daily.iloc[:, [0, 1]].copy()
    nav.columns = ["code", "price_name"]
    nav["reference_price"] = pd.to_numeric(daily[nav_column], errors="coerce")
    nav["reference_price_type"] = "NAV"
    nav["code"] = nav["code"].astype(str).str.zfill(6)
    nav = nav.drop_duplicates("code", keep="last")

    spot = retry("Eastmoney ETF spot cross-check", ak.fund_etf_spot_em)
    required = ["代码", "最新价", "最新份额", "数据日期", "更新时间"]
    if any(column not in spot.columns for column in required):
        raise ValueError("ETF spot response schema changed")
    spot = spot[required].copy()
    spot.columns = ["code", "same_day_close", "live_shares", "data_date", "updated_at"]
    spot["code"] = spot["code"].astype(str).str.zfill(6)
    spot["data_date"] = pd.to_datetime(spot["data_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    spot = spot[spot["data_date"] == day.isoformat()].copy()
    spot["same_day_close"] = pd.to_numeric(spot["same_day_close"], errors="coerce")
    spot["live_shares"] = pd.to_numeric(spot["live_shares"], errors="coerce")
    spot = spot.drop_duplicates("code", keep="last")

    out = nav.merge(spot, on="code", how="outer", validate="one_to_one")
    fallback = out["reference_price"].isna() & out["same_day_close"].notna()
    out.loc[fallback, "reference_price"] = out.loc[fallback, "same_day_close"]
    out.loc[fallback, "reference_price_type"] = "CLOSE"
    return out[["code", "reference_price", "reference_price_type", "live_shares", "data_date", "updated_at"]]


def identify_index(name: str) -> tuple[str, dict[str, Any]] | None:
    for code, meta in MAPPING.items():
        if any(re.search(pattern, name, re.IGNORECASE) for pattern in meta["patterns"]):
            return code, meta
    return None


def percentile(values: list[float], current: float) -> float | None:
    valid = [x for x in values if math.isfinite(x)]
    if len(valid) < 60:
        return None
    return 100 * sum(x <= current for x in valid) / len(valid)


def historical_flows(exclude_date: str) -> dict[str, list[float]]:
    history: dict[str, list[float]] = {}
    for path in sorted((PUBLIC / "history").glob("*.json")):
        try:
            snapshot = json.loads(path.read_text("utf-8"))
            if snapshot.get("tradeDate") == exclude_date or snapshot.get("status") == "failed":
                continue
            for item in snapshot.get("indices", []):
                value = item.get("flow1d")
                if isinstance(value, (int, float)) and math.isfinite(value):
                    history.setdefault(str(item.get("code")), []).append(float(value))
        except (OSError, ValueError, TypeError):
            continue
    return history


def status_from_percentile(position: float | None) -> str:
    if position is None:
        return "样本积累中"
    if position >= 90:
        return "强流入"
    if position >= 70:
        return "偏流入"
    if position <= 10:
        return "强流出"
    if position <= 30:
        return "偏流出"
    return "中性"


def build_snapshot(day: date, current: pd.DataFrame | None = None) -> dict[str, Any]:
    current = current if current is not None else fetch_exchange_shares(day)
    previous_day, previous_all = fetch_available_shares(day - timedelta(days=1))
    previous = previous_all[["code", "shares"]].rename(columns={"shares": "previous_shares"})
    prices = fetch_reference_prices(day)
    merged = current.merge(previous, on="code", how="left", validate="one_to_one").merge(
        prices, on="code", how="left", validate="one_to_one"
    )

    issues: list[dict[str, str]] = []
    if merged["code"].duplicated().any():
        issues.append({"severity": "critical", "check": "unique_code", "message": "同一交易日存在重复基金代码"})
    if len(merged) < 500:
        issues.append({"severity": "critical", "check": "market_coverage", "message": f"ETF覆盖仅 {len(merged)} 只，低于安全阈值 500"})
    if merged["shares"].isna().any() or (merged["shares"] <= 0).any():
        issues.append({"severity": "critical", "check": "valid_shares", "message": "官方份额存在缺失或非正值"})
    previous_coverage = float(merged["previous_shares"].notna().mean())
    if previous_coverage < 0.95:
        issues.append({"severity": "critical", "check": "previous_share_coverage", "message": f"前一交易日份额覆盖率 {previous_coverage:.1%} 低于 95%"})
    price_coverage = float(merged["reference_price"].notna().mean())
    if price_coverage < 0.95:
        issues.append({"severity": "critical", "check": "price_coverage", "message": f"同日参考价格覆盖率 {price_coverage:.1%} 低于 95%"})

    comparable = merged.dropna(subset=["live_shares"])
    reconcile_rate: float | None = None
    if len(comparable) >= 500:
        comparable = comparable.assign(diff=(comparable["shares"] - comparable["live_shares"]).abs() / comparable["shares"])
        reconcile_rate = float((comparable["diff"] <= 0.001).mean())
        if reconcile_rate < 0.95:
            issues.append({"severity": "critical", "check": "share_reconciliation", "message": f"同日交易所与行情端份额一致率 {reconcile_rate:.1%} 低于 95%"})
    else:
        issues.append({"severity": "info", "check": "share_reconciliation", "message": "行情端没有足量同日份额，已跳过跨日对账；官方交易所份额仍为唯一计算口径"})

    merged["share_change"] = merged["shares"] - merged["previous_shares"]
    merged["share_change_pct"] = merged["share_change"] / merged["previous_shares"] * 100
    merged["flow"] = merged["share_change"] * merged["reference_price"]
    core: list[dict[str, Any]] = []
    etfs: list[dict[str, Any]] = []
    for row in merged.itertuples(index=False):
        match = identify_index(str(row.name))
        if not match or pd.isna(row.previous_shares) or pd.isna(row.reference_price):
            continue
        index_code, meta = match
        etfs.append({
            "code": row.code,
            "name": row.name,
            "exchange": row.exchange,
            "indexCode": index_code,
            "indexName": meta["name"],
            "group": meta["group"],
            "shares": round(float(row.shares), 2),
            "previousShares": round(float(row.previous_shares), 2),
            "shareChangePct": round(float(row.share_change_pct), 4),
            "referencePrice": round(float(row.reference_price), 4),
            "referencePriceType": str(row.reference_price_type),
            "estimatedFlow": round(float(row.flow), 2),
            "source": "SSE/SZSE day-end shares + same-day NAV via AKShare",
        })

    etf_df = pd.DataFrame(etfs)
    history = historical_flows(day.isoformat())
    if not etf_df.empty:
        for index_code, group in etf_df.groupby("indexCode"):
            meta = MAPPING[index_code]
            flow = round(float(group["estimatedFlow"].sum()) / 1e8, 2)
            prior = history.get(index_code, [])
            observations = prior + [flow]
            position = percentile(prior[-250:], flow)
            core.append({
                "code": index_code,
                "name": meta["name"],
                "group": meta["group"],
                "flow1d": flow,
                "flow5d": round(sum(observations[-5:]), 2) if len(observations) >= 5 else None,
                "flow20d": round(sum(observations[-20:]), 2) if len(observations) >= 20 else None,
                "shareChangePct": round(float(group["shareChangePct"].median()), 3),
                "etfCount": int(len(group)),
                "percentile": round(position, 1) if position is not None else None,
                "status": status_from_percentile(position),
                "spark": [round(value, 2) for value in observations[-12:]],
                "nationalTeamProxy": bool(meta["national_team_proxy"]),
            })

    critical = any(issue["severity"] == "critical" for issue in issues)
    return {
        "schemaVersion": 2,
        "status": "failed" if critical else ("warning" if any(issue["severity"] != "info" for issue in issues) else "verified"),
        "tradeDate": day.isoformat(),
        "previousTradeDate": previous_day.isoformat(),
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourceMode": "REAL",
        "quality": {
            "marketEtfCount": int(len(merged)),
            "previousShareCoverage": round(previous_coverage, 4),
            "priceCoverage": round(price_coverage, 4),
            "shareReconciliationRate": round(reconcile_rate, 4) if reconcile_rate is not None else None,
            "mappedEtfCount": len(etfs),
            "issues": issues,
        },
        "sources": [
            {"name": "上海证券交易所", "field": "沪市ETF日终总份额", "role": "官方计算主源"},
            {"name": "深圳证券交易所", "field": "深市ETF日终总份额", "role": "官方计算主源"},
            {"name": "东方财富/AKShare", "field": f"{day.isoformat()} 单位净值；同日行情可用时交叉核验份额", "role": "同日参考价格与核验"},
        ],
        "indices": sorted(core, key=lambda item: item["flow1d"], reverse=True),
        "etfs": sorted(etfs, key=lambda item: abs(item["estimatedFlow"]), reverse=True),
        "methodology": "Estimated Flow = (Shares_t - Shares_t-1) × same-day NAV; same-day close is used only when NAV is missing.",
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
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to latest official trading day")
    args = parser.parse_args()
    try:
        if args.date:
            day = date.fromisoformat(args.date)
            current = fetch_exchange_shares(day)
        else:
            day, current = fetch_available_shares(latest_weekday(date.today() - timedelta(days=1)))
        snapshot = build_snapshot(day, current=current)
        path = atomic_publish(snapshot)
    except Exception as exc:
        print(f"UPDATE FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"verified snapshot: {path} ({snapshot['tradeDate']}, {len(snapshot['etfs'])} mapped ETFs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
