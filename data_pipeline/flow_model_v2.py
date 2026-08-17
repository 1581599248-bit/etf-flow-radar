"""Canonical ETF flow model for schema v6.

One economic fact sits at the centre of this module: the change in exchange
end-of-day ETF shares after corporate actions have been restated to comparable
units.  Everything else is an explicit dimension around that fact:

* primary-market valuation: same-day NAV (canonical) or average traded price
  (comparison only);
* market scope: all ETFs, stock ETFs including cross-border, or domestic A-share
  stock ETFs;
* secondary-market order flow: a separate trading statistic that never
  overwrites primary creation/redemption.

Canonical one-day primary metric:
    (T exchange shares - comparable T-1 shares) * T unit NAV

All public monetary fields are expressed in 亿元.
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

import pandas as pd

import update_daily as base

_CROSS_BORDER = re.compile(base.CONFIG["globalExcludePatterns"][0], re.IGNORECASE)
# The legacy global exclusion regex combines bonds and some cash-management ETF
# names.  Asset classification must be mutually exclusive, so money is tested
# first and removed from the bond decision explicitly.
_MONEY = re.compile(
    r"货币(?:ETF|基金)?|快线|保证金|收益快线|添富快线|日鑫|理财金|快钱",
    re.IGNORECASE,
)
_BOND = re.compile(
    r"国债|政金债|信用债|公司债|城投债|地方债|短融|可转债|转债|科创债|债券|债ETF|"
    r"同业存单|国开债|地债|科债|城投",
    re.IGNORECASE,
)
_COMMODITY = re.compile(base.CONFIG["globalExcludePatterns"][2], re.IGNORECASE)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _asset_scope(name: str, fund_name: str, fund_type: str) -> str:
    """Return one and only one mutually exclusive ETF asset scope."""
    text = f"{name} {fund_name}".strip()
    kind = str(fund_type).strip()
    if kind == "股票型":
        return "crossBorderStockEtf" if _CROSS_BORDER.search(text) else "aShareStockEtf"
    if kind == "货币型" or _MONEY.search(text):
        return "moneyEtf"
    if kind == "债券型" or _BOND.search(text):
        return "bondEtf"
    if _COMMODITY.search(text):
        return "commodityEtf"
    return "otherEtf"


def _direction_counts(frame: pd.DataFrame, delta_col: str) -> dict[str, int]:
    values = pd.to_numeric(frame[delta_col], errors="coerce")
    return {
        "increase": int((values > 0).sum()),
        "decrease": int((values < 0).sum()),
        "unchanged": int((values == 0).sum()),
    }


def _aggregate_scope(frame: pd.DataFrame, name: str) -> dict[str, Any]:
    valid = frame.dropna(subset=["share_delta_1d", "nav", "primary_flow_1d"]).copy()
    if valid.empty:
        return {
            "name": name,
            "etfCount": 0,
            "etfCount5d": 0,
            "etfCount20d": 0,
            "flow1d": None,
            "flow5dEndpoint": None,
            "flow20dEndpoint": None,
            "aum": None,
            "increaseEtfCount1d": 0,
            "decreaseEtfCount1d": 0,
            "unchangedEtfCount1d": 0,
        }
    counts = _direction_counts(valid, "share_delta_1d")
    five = valid.dropna(subset=["primary_flow_5d_endpoint"])
    twenty = valid.dropna(subset=["primary_flow_20d_endpoint"])
    top_in = valid.loc[valid["primary_flow_1d"].idxmax()]
    top_out = valid.loc[valid["primary_flow_1d"].idxmin()]
    return {
        "name": name,
        "etfCount": int(len(valid)),
        "etfCount5d": int(len(five)),
        "etfCount20d": int(len(twenty)),
        "flow1d": round(float(valid["primary_flow_1d"].sum()), 2),
        "flow5dEndpoint": round(float(five["primary_flow_5d_endpoint"].sum()), 2) if not five.empty else None,
        "flow20dEndpoint": round(float(twenty["primary_flow_20d_endpoint"].sum()), 2) if not twenty.empty else None,
        "aum": round(float((valid["shares"] * valid["nav"] / 1e8).sum()), 2),
        "breadth1d": round((counts["increase"] - counts["decrease"]) / len(valid) * 100, 1),
        "increaseEtfCount1d": counts["increase"],
        "decreaseEtfCount1d": counts["decrease"],
        "unchangedEtfCount1d": counts["unchanged"],
        "unchangedEtfPct1d": round(counts["unchanged"] / len(valid) * 100, 2),
        "topInflowEtf": {
            "code": str(top_in["code"]),
            "name": str(top_in["name"]),
            "flow1d": round(float(top_in["primary_flow_1d"]), 2),
        },
        "topOutflowEtf": {
            "code": str(top_out["code"]),
            "name": str(top_out["name"]),
            "flow1d": round(float(top_out["primary_flow_1d"]), 2),
        },
    }


def _secondary_order_flow(
    frame: pd.DataFrame,
    spot: pd.DataFrame | None,
    day: date,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Build secondary trading flow only when the provider date exactly matches."""
    empty: dict[str, Any] = {
        "metric": "secondaryMarketMainOrderFlow",
        "definition": "交易所二级市场成交中的主力净流入/净流出统计；不是ETF申购赎回。",
        "source": "东方财富ETF行情 主力净流入-净额",
        "status": "unavailable",
        "tradeDate": day.isoformat(),
        "scopeTotals": {},
    }
    if spot is None or spot.empty:
        empty["reason"] = "no same-day order-flow snapshot"
        return empty, {}

    source = spot.copy()
    source.columns = [str(c).strip() for c in source.columns]
    required = {"代码", "主力净流入-净额", "数据日期"}
    if not required.issubset(source.columns):
        empty["reason"] = f"missing columns: {sorted(required - set(source.columns))}"
        return empty, {}

    source["code"] = source["代码"].astype(str).str.zfill(6)
    source["data_date"] = pd.to_datetime(source["数据日期"], errors="coerce").dt.date
    source["secondary_order_flow"] = pd.to_numeric(source["主力净流入-净额"], errors="coerce") / 1e8
    exact = source[source["data_date"] == day].dropna(subset=["secondary_order_flow"])
    exact = exact.drop_duplicates("code", keep="last")
    if exact.empty:
        dates = sorted({x.isoformat() for x in source["data_date"].dropna().tolist()})
        empty["reason"] = "provider snapshot date does not match requested trade date"
        empty["providerDate"] = dates[-1] if dates else None
        return empty, {}

    joined = frame[["code", "scope"]].merge(
        exact[["code", "secondary_order_flow"]], on="code", how="inner"
    )
    per_etf = dict(zip(joined["code"], joined["secondary_order_flow"]))

    def total(mask: pd.Series) -> dict[str, Any]:
        part = joined[mask]
        return {
            "etfCount": int(len(part)),
            "flow1d": round(float(part["secondary_order_flow"].sum()), 2),
        }

    all_mask = pd.Series(True, index=joined.index)
    stock_mask = joined["scope"].isin(["aShareStockEtf", "crossBorderStockEtf"])
    ashare_mask = joined["scope"].eq("aShareStockEtf")
    return {
        **empty,
        "status": "available",
        "scopeTotals": {
            "allEtf": total(all_mask),
            "stockEtfIncludingCrossBorder": total(stock_mask),
            "aShareStockEtf": total(ashare_mask),
        },
    }, per_etf


def _recalculate_groups(snapshot: dict[str, Any]) -> None:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for record in snapshot.get("etfs", []):
        if isinstance(record.get("flow1d"), (int, float)):
            by_group.setdefault(str(record.get("groupId")), []).append(record)

    for group in snapshot.get("groups", []):
        members = by_group.get(str(group.get("id")), [])
        if not members:
            continue
        f1 = sum(float(x.get("flow1d") or 0) for x in members)
        f5 = sum(float(x.get("flow5dEndpoint") or 0) for x in members if x.get("flow5dEndpoint") is not None)
        f20 = sum(float(x.get("flow20dEndpoint") or 0) for x in members if x.get("flow20dEndpoint") is not None)
        aum = sum(float(x.get("aum") or 0) for x in members)
        prior1 = sum(
            float(x.get("previousComparableShares") or 0) * float(x.get("nav") or 0) / 1e8
            for x in members
        )
        prior5 = sum(
            float(x.get("shares5dAgoComparable") or 0) * float(x.get("nav") or 0) / 1e8
            for x in members
        )
        prior20 = sum(
            float(x.get("shares20dAgoComparable") or 0) * float(x.get("nav") or 0) / 1e8
            for x in members
        )
        delta1 = pd.Series([float(x.get("shareDelta1d") or 0) for x in members])
        delta5 = pd.Series([float(x.get("shareDelta5dEndpoint") or 0) for x in members])
        gross = sum(abs(float(x.get("flow1d") or 0)) for x in members)
        dominant = max(members, key=lambda x: abs(float(x.get("flow1d") or 0)))
        intensity1 = f1 / max(prior1, 0.01) * 100
        intensity5 = f5 / max(prior5, 0.01) * 100 if prior5 else 0.0
        intensity20 = f20 / max(prior20, 0.01) * 100 if prior20 else 0.0
        group.update({
            "flow1d": round(f1, 2),
            "flow5d": round(f5, 2),
            "flow20d": round(f20, 2),
            "flow5dMetric": "endpointShareChangeTimesCurrentNAV",
            "flow20dMetric": "endpointShareChangeTimesCurrentNAV",
            "aum": round(aum, 2),
            "etfCount": len(members),
            "flowIntensity1dPct": round(intensity1, 2),
            "flowIntensity5dPct": round(intensity5, 2),
            "flowIntensity20dPct": round(intensity20, 2),
            "flowIntensity1dBps": round(intensity1 * 100, 1),
            "flowIntensity5dBps": round(intensity5 * 100, 1),
            "flowIntensity20dBps": round(intensity20 * 100, 1),
            "breadth1d": round(((delta1 > 0).sum() - (delta1 < 0).sum()) / len(members) * 100, 1),
            "breadth5d": round(((delta5 > 0).sum() - (delta5 < 0).sum()) / len(members) * 100, 1),
            "increaseEtfCount1d": int((delta1 > 0).sum()),
            "decreaseEtfCount1d": int((delta1 < 0).sum()),
            "unchangedEtfCount1d": int((delta1 == 0).sum()),
            "increaseEtfCount5d": int((delta5 > 0).sum()),
            "decreaseEtfCount5d": int((delta5 < 0).sum()),
            "unchangedEtfCount5d": int((delta5 == 0).sum()),
            "concentration1d": round(abs(float(dominant.get("flow1d") or 0)) / gross * 100, 1) if gross else 0.0,
            "dominantEtf": {
                "code": str(dominant.get("code")),
                "name": str(dominant.get("name")),
                "flow1d": round(float(dominant.get("flow1d") or 0), 2),
            },
        })
        relative = group.get("relativeReturn20d")
        if isinstance(relative, (int, float)):
            group["priceFlowState"] = base._flow_state(float(relative), intensity5)


def apply_flow_model(
    snapshot: dict[str, Any],
    day: date,
    share_window: list[tuple[date, pd.DataFrame]],
    ths: pd.DataFrame,
    spot: pd.DataFrame | None = None,
) -> None:
    if len(share_window) < 2:
        raise ValueError("flow model v2 requires at least T and T-1 share observations")
    universe = pd.DataFrame(snapshot.get("universe", []))
    required = {"code", "name", "shares"}
    if universe.empty or not required.issubset(universe.columns):
        raise ValueError("snapshot universe does not contain code/name/shares")

    columns = [
        c for c in ["code", "name", "shares", "referencePrice", "referencePriceType"]
        if c in universe.columns
    ]
    frame = universe[columns].copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce")
    frame = frame.merge(
        ths[["code", "fund_name", "fund_type", "nav", "prev_nav"]],
        on="code",
        how="left",
    )
    for column in ("nav", "prev_nav"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    dates = [d for d, _ in share_window]
    maps = {d: f.set_index("code")["shares"] for d, f in share_window}
    frame["previous_comparable_shares"] = frame["code"].map(maps[dates[-2]])
    frame["share_delta_1d"] = frame["shares"] - frame["previous_comparable_shares"]
    frame["primary_flow_1d"] = frame["share_delta_1d"] * frame["nav"] / 1e8
    frame["scope"] = frame.apply(
        lambda row: _asset_scope(
            str(row["name"]), str(row.get("fund_name", "")), str(row.get("fund_type", ""))
        ),
        axis=1,
    )

    if len(dates) >= 6:
        frame["shares_5d_ago"] = frame["code"].map(maps[dates[-6]])
        frame["share_delta_5d_endpoint"] = frame["shares"] - frame["shares_5d_ago"]
        frame["primary_flow_5d_endpoint"] = frame["share_delta_5d_endpoint"] * frame["nav"] / 1e8
    else:
        frame["shares_5d_ago"] = math.nan
        frame["share_delta_5d_endpoint"] = math.nan
        frame["primary_flow_5d_endpoint"] = math.nan
    if len(dates) >= 21:
        frame["shares_20d_ago"] = frame["code"].map(maps[dates[-21]])
        frame["share_delta_20d_endpoint"] = frame["shares"] - frame["shares_20d_ago"]
        frame["primary_flow_20d_endpoint"] = frame["share_delta_20d_endpoint"] * frame["nav"] / 1e8
    else:
        frame["shares_20d_ago"] = math.nan
        frame["share_delta_20d_endpoint"] = math.nan
        frame["primary_flow_20d_endpoint"] = math.nan

    if "referencePrice" in frame.columns:
        frame["referencePrice"] = pd.to_numeric(frame["referencePrice"], errors="coerce")
        frame["flow_avg_price_estimate_1d"] = frame["share_delta_1d"] * frame["referencePrice"] / 1e8
    else:
        frame["flow_avg_price_estimate_1d"] = math.nan

    scope_frames = {
        "allEtf": frame,
        "stockEtfIncludingCrossBorder": frame[
            frame["scope"].isin(["aShareStockEtf", "crossBorderStockEtf"])
        ],
        "aShareStockEtf": frame[frame["scope"].eq("aShareStockEtf")],
    }
    scope_totals = {
        "allEtf": _aggregate_scope(scope_frames["allEtf"], "全部场内ETF"),
        "stockEtfIncludingCrossBorder": _aggregate_scope(
            scope_frames["stockEtfIncludingCrossBorder"], "股票ETF（含跨境）"
        ),
        "aShareStockEtf": _aggregate_scope(scope_frames["aShareStockEtf"], "A股股票ETF"),
    }
    fund_type_totals: list[dict[str, Any]] = []
    for fund_type, part in frame.groupby(frame["fund_type"].fillna("未知").astype(str).str.strip()):
        fund_type_totals.append({"fundType": str(fund_type), **_aggregate_scope(part, str(fund_type))})

    secondary, secondary_per_etf = _secondary_order_flow(frame, spot, day)
    snapshot["flowMetrics"] = {
        "primaryMarket": {
            "metric": "primaryMarketNetSubscriptionEstimate",
            "displayName": "一级市场净申购/赎回估算",
            "valuation": "sameDayUnitNAV",
            "definition": "（T日交易所日终份额－T-1日公司行动调整后的可比份额）×T日单位净值。",
            "tradeDate": day.isoformat(),
            "scopeTotals": scope_totals,
            "fundTypeTotals": fund_type_totals,
            "multiDay": {
                "fiveDayField": "flow5dEndpoint",
                "twentyDayField": "flow20dEndpoint",
                "definition": "当前5日/20日为端点份额变化×期末NAV，不是逐日净申购额之和；待每日v2记录积累后再发布真正累计值。",
            },
        },
        "secondaryMarketOrderFlow": secondary,
    }

    market = scope_totals["aShareStockEtf"]
    snapshot["market"] = {
        **market,
        "flow5d": market.get("flow5dEndpoint"),
        "flow20d": market.get("flow20dEndpoint"),
        "metric": "primaryMarketNetSubscriptionEstimate",
        "valuation": "sameDayUnitNAV",
        "scopeKey": "aShareStockEtf",
        "scope": "domestic_a_share_stock_etf",
        "multiDayMethod": "endpoint_share_change_times_current_nav",
    }

    by_code = frame.set_index("code", drop=False)
    updated_records: list[dict[str, Any]] = []
    for record in snapshot.get("etfs", []):
        code = str(record.get("code"))
        if code not in by_code.index:
            continue
        row = by_code.loc[code]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        if not _finite(row.get("primary_flow_1d")):
            continue
        record.update({
            "shares": round(float(row["shares"]), 2),
            "previousComparableShares": round(float(row["previous_comparable_shares"]), 2),
            "shareDelta1d": round(float(row["share_delta_1d"]), 2),
            "nav": round(float(row["nav"]), 4),
            "previousNav": round(float(row["prev_nav"]), 4) if _finite(row.get("prev_nav")) else None,
            "flow1d": round(float(row["primary_flow_1d"]), 2),
            "primaryFlow1d": round(float(row["primary_flow_1d"]), 2),
            "flow1dAvgPriceEstimate": round(float(row["flow_avg_price_estimate_1d"]), 2)
            if _finite(row.get("flow_avg_price_estimate_1d")) else None,
            "shares5dAgoComparable": round(float(row["shares_5d_ago"]), 2)
            if _finite(row.get("shares_5d_ago")) else None,
            "shareDelta5dEndpoint": round(float(row["share_delta_5d_endpoint"]), 2)
            if _finite(row.get("share_delta_5d_endpoint")) else None,
            "flow5dEndpoint": round(float(row["primary_flow_5d_endpoint"]), 2)
            if _finite(row.get("primary_flow_5d_endpoint")) else None,
            "shares20dAgoComparable": round(float(row["shares_20d_ago"]), 2)
            if _finite(row.get("shares_20d_ago")) else None,
            "shareDelta20dEndpoint": round(float(row["share_delta_20d_endpoint"]), 2)
            if _finite(row.get("share_delta_20d_endpoint")) else None,
            "flow20dEndpoint": round(float(row["primary_flow_20d_endpoint"]), 2)
            if _finite(row.get("primary_flow_20d_endpoint")) else None,
            "flow5d": round(float(row["primary_flow_5d_endpoint"]), 2)
            if _finite(row.get("primary_flow_5d_endpoint")) else None,
            "flow20d": round(float(row["primary_flow_20d_endpoint"]), 2)
            if _finite(row.get("primary_flow_20d_endpoint")) else None,
            "aum": round(float(row["shares"] * row["nav"] / 1e8), 2),
            "flowMetric": "primaryMarketNetSubscriptionEstimate",
            "flowValuation": "sameDayUnitNAV",
            "secondaryMainOrderFlow1d": round(float(secondary_per_etf[code]), 2)
            if code in secondary_per_etf else None,
        })
        updated_records.append(record)
    snapshot["etfs"] = sorted(
        updated_records,
        key=lambda x: abs(float(x.get("flow1d") or 0)),
        reverse=True,
    )

    frame_records = frame.set_index("code").to_dict("index")
    for record in snapshot.get("universe", []):
        code = str(record.get("code"))
        row = frame_records.get(code)
        if not row:
            continue
        record.update({
            "assetScope": row.get("scope"),
            "fundType": None if pd.isna(row.get("fund_type")) else str(row.get("fund_type")),
            "nav": round(float(row["nav"]), 4) if _finite(row.get("nav")) else None,
            "previousNav": round(float(row["prev_nav"]), 4) if _finite(row.get("prev_nav")) else None,
            "previousComparableShares": round(float(row["previous_comparable_shares"]), 2)
            if _finite(row.get("previous_comparable_shares")) else None,
            "shareDelta1d": round(float(row["share_delta_1d"]), 2)
            if _finite(row.get("share_delta_1d")) else None,
            "primaryFlow1d": round(float(row["primary_flow_1d"]), 2)
            if _finite(row.get("primary_flow_1d")) else None,
        })

    _recalculate_groups(snapshot)
    quality = snapshot.setdefault("quality", {})
    quality["flowModelVersion"] = 2
    quality["canonicalFlowValuation"] = "sameDayUnitNAV"
    quality["primaryMarketScopeCount"] = scope_totals["aShareStockEtf"].get("etfCount")
    quality["secondaryOrderFlowStatus"] = secondary.get("status")
    quality["metricSeparation"] = "primary_market_subscription_vs_secondary_market_order_flow"
