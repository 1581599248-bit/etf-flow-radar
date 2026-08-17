"""Single production entrypoint for ETF Flow Radar schema v6.

Transport and guard layers remain in the older modules. This file is the single
public production entrypoint and the single client-facing schema layer. Archived
rebuilds can call ``apply_v2_semantics`` with validated local share data, so a
methodology-only JSON migration does not need to redownload historical exchange
files.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import update_daily as base
import update_daily_guarded as guarded
import update_daily_production as production
import flow_model_v2
import flow_comparison_v2
import flow_scope_breakdown_v2

_ORIG_POSTPROCESS = production._postprocess_snapshot
_ORIG_ATOMIC_PUBLISH = base.atomic_publish


def _load_secondary_spot(day: date) -> pd.DataFrame:
    path = base.PUBLIC / "order_flow" / f"{day.isoformat()}.json"
    if path.exists():
        payload = json.loads(path.read_text("utf-8"))
        if (
            payload.get("tradeDate") == day.isoformat()
            and payload.get("metric") in {"secondaryMarketMainOrderFlow", "secondaryMarketETFTradingFlow"}
        ):
            rows = payload.get("etfs", [])
            if rows:
                frame = pd.DataFrame(rows)
                frame["代码"] = frame["code"].astype(str).str.zfill(6)
                frame["名称"] = frame["name"].astype(str)
                frame["数据日期"] = day.isoformat()
                if "mainOrderFlow1d" in frame.columns:
                    frame["主力净流入-净额"] = pd.to_numeric(frame["mainOrderFlow1d"], errors="coerce") * 1e8
                if "tradeNetFlow1d" in frame.columns:
                    frame["当日交易净额"] = pd.to_numeric(frame["tradeNetFlow1d"], errors="coerce") * 1e8
                if "tradeInflow1d" in frame.columns:
                    frame["当日交易流入"] = pd.to_numeric(frame["tradeInflow1d"], errors="coerce") * 1e8
                if "tradeOutflow1d" in frame.columns:
                    frame["当日交易流出"] = pd.to_numeric(frame["tradeOutflow1d"], errors="coerce") * 1e8
                if "amount" in frame.columns:
                    frame["成交额"] = pd.to_numeric(frame["amount"], errors="coerce") * 1e8
                keep = [
                    c for c in [
                        "代码", "名称", "主力净流入-净额", "当日交易净额",
                        "当日交易流入", "当日交易流出", "成交额", "数据日期",
                    ] if c in frame.columns
                ]
                return frame[keep]
    # Live spot is accepted downstream only if its own provider date equals day.
    return guarded._get_spot()


def _same_day_trade_rows(spot: pd.DataFrame | None, day: date) -> pd.DataFrame:
    """Return same-day per-ETF all-trade net flow in yuan.

    New persisted facts already contain the value. For a live same-day fallback,
    split turnover by the provider's outer/inner active-trade volume ratio.
    """
    columns = ["code", "trade_net_yuan", "trade_in_yuan", "trade_out_yuan"]
    if spot is None or spot.empty:
        return pd.DataFrame(columns=columns)
    source = spot.copy()
    source.columns = [str(c).strip() for c in source.columns]
    if not {"代码", "数据日期"}.issubset(source.columns):
        return pd.DataFrame(columns=columns)
    source["code"] = source["代码"].astype(str).str.zfill(6)
    source["data_date"] = pd.to_datetime(source["数据日期"], errors="coerce").dt.date
    source = source[source["data_date"] == day].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)

    if "当日交易净额" in source.columns:
        source["trade_net_yuan"] = pd.to_numeric(source["当日交易净额"], errors="coerce")
        source["trade_in_yuan"] = pd.to_numeric(source.get("当日交易流入"), errors="coerce")
        source["trade_out_yuan"] = pd.to_numeric(source.get("当日交易流出"), errors="coerce")
    elif {"成交额", "外盘", "内盘"}.issubset(source.columns):
        amount = pd.to_numeric(source["成交额"], errors="coerce")
        outer = pd.to_numeric(source["外盘"], errors="coerce")
        inner = pd.to_numeric(source["内盘"], errors="coerce")
        directional = outer + inner
        valid = directional > 0
        source["trade_in_yuan"] = amount * outer / directional.where(valid)
        source["trade_out_yuan"] = amount * inner / directional.where(valid)
        source["trade_net_yuan"] = source["trade_in_yuan"] - source["trade_out_yuan"]
    else:
        return pd.DataFrame(columns=columns)

    return source[columns].dropna(subset=["trade_net_yuan"]).drop_duplicates("code", keep="last")


def _add_trade_net_flow(
    snapshot: dict[str, Any], day: date, ths: pd.DataFrame, spot: pd.DataFrame | None
) -> None:
    """Add the user's first-layer metric without changing the share-flow model."""
    target = {
        "metric": "secondaryMarketTradeNetFlowEstimate",
        "displayName": "当日交易净流入/净流出",
        "definition": "按同日ETF成交额与外盘/内盘主动成交方向估算交易资金净额；只显示主动买入金额减主动卖出金额的差额。",
        "source": "东方财富ETF同日行情 成交额 + 外盘/内盘",
        "tradeDate": day.isoformat(),
        "status": "unavailable",
        "scopeTotals": {},
    }
    exact = _same_day_trade_rows(spot, day)
    if exact.empty:
        target["reason"] = "no same-day all-trade direction snapshot"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return

    universe = pd.DataFrame(snapshot.get("universe", []))
    if universe.empty or not {"code", "name"}.issubset(universe.columns):
        target["reason"] = "snapshot universe missing code/name"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return
    frame = universe[["code", "name"]].copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame.drop_duplicates("code", keep="last").merge(
        ths[["code", "fund_name", "fund_type"]], on="code", how="left"
    )
    frame["scope"] = frame.apply(
        lambda row: flow_model_v2._asset_scope(
            str(row["name"]), str(row.get("fund_name", "")), str(row.get("fund_type", ""))
        ),
        axis=1,
    )
    joined = frame[["code", "scope"]].merge(exact, on="code", how="inner")
    if joined.empty:
        target["reason"] = "same-day trading rows do not overlap the ETF universe"
        snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target
        return

    def total(part: pd.DataFrame) -> dict[str, Any]:
        inflow = pd.to_numeric(part["trade_in_yuan"], errors="coerce")
        outflow = pd.to_numeric(part["trade_out_yuan"], errors="coerce")
        return {
            "etfCount": int(len(part)),
            "netFlow1d": round(float(part["trade_net_yuan"].sum()) / 1e8, 2),
            "inflow1d": round(float(inflow.sum()) / 1e8, 2) if inflow.notna().any() else None,
            "outflow1d": round(float(outflow.sum()) / 1e8, 2) if outflow.notna().any() else None,
        }

    target.update({
        "status": "available",
        "scopeTotals": {
            "allEtf": total(joined),
            "stockEtfIncludingCrossBorder": total(
                joined[joined["scope"].isin(["aShareStockEtf", "crossBorderStockEtf"])]
            ),
            "aShareStockEtf": total(joined[joined["scope"].eq("aShareStockEtf")]),
        },
    })
    snapshot.setdefault("flowMetrics", {})["secondaryMarketTradeFlow"] = target

    trade_map = dict(zip(joined["code"], joined["trade_net_yuan"] / 1e8))
    for item in snapshot.get("etfs", []):
        value = trade_map.get(str(item.get("code", "")).zfill(6))
        item["secondaryTradeNetFlow1d"] = round(float(value), 4) if pd.notna(value) else None


def _flow_phrase(value: float) -> str:
    if value > 0:
        return f"净流入{value:.1f}亿元"
    if value < 0:
        return f"净流出{abs(value):.1f}亿元"
    return "净额0.0亿元"


def _regenerate_v2_conclusion(snapshot: dict[str, Any]) -> None:
    """Headline = same-day trading net flow + same-day ETF share-flow change."""
    production._regenerate_conclusion(snapshot)
    old = str(snapshot.get("conclusion", {}).get("headline") or "")
    tail = ""
    if "宽基中" in old:
        tail = "宽基中" + old.split("宽基中", 1)[1]

    market = snapshot["market"]
    share_value = float(market.get("flow1d") or 0)
    trade_scope = (
        snapshot.get("flowMetrics", {})
        .get("secondaryMarketTradeFlow", {})
        .get("scopeTotals", {})
        .get("aShareStockEtf", {})
    )
    trade_value = trade_scope.get("netFlow1d")
    if isinstance(trade_value, (int, float)):
        first = f"A股ETF当日交易{_flow_phrase(float(trade_value))}；"
    else:
        first = "A股ETF当日交易净额暂无同日数据；"
    first += (
        f"ETF份额{_flow_phrase(share_value)}，"
        f"{market.get('increaseEtfCount1d', 0)}只份额增加、"
        f"{market.get('decreaseEtfCount1d', 0)}只份额减少、"
        f"{market.get('unchangedEtfCount1d', 0)}只不变。"
    )
    snapshot.setdefault("conclusion", {})["headline"] = first + tail


def apply_v2_semantics(
    snapshot: dict[str, Any],
    day: date,
    share_window: list[tuple[date, pd.DataFrame]],
    ths: pd.DataFrame,
    spot: pd.DataFrame | None,
) -> None:
    """Apply every client-facing schema-v6 flow rule exactly once."""
    legacy_classified_count = snapshot.get("quality", {}).get("classifiedEtfCount")

    flow_model_v2.apply_flow_model(snapshot, day, share_window, ths, spot)
    flow_comparison_v2.add_primary_valuation_comparisons(snapshot)
    flow_scope_breakdown_v2.add_asset_class_totals(snapshot)
    _add_trade_net_flow(snapshot, day, ths, spot)

    primary = snapshot.setdefault("flowMetrics", {}).setdefault("primaryMarket", {})
    primary["displayName"] = "ETF份额净流入/净流出"
    primary["definition"] = "（T日交易所日终份额－T-1日公司行动调整后的可比份额）×T日单位净值。"

    rollups = production._build_industry_rollups(snapshot)
    snapshot["industryRollups"] = rollups
    snapshot["themeGroups"] = [
        g for g in snapshot.get("groups", []) if g.get("kind") == "industry" and g.get("parent")
    ]

    market = snapshot["market"]
    classified_flow = round(sum(float(g.get("flow1d", 0) or 0) for g in snapshot.get("groups", [])), 2)
    quality = snapshot.setdefault("quality", {})
    quality.update({
        "industryRollupCount": len(rollups),
        "themeGroupCount": len(snapshot["themeGroups"]),
        "legacyClassifiedEtfCountBeforeV6": legacy_classified_count,
        "classifiedEtfCount": len(snapshot.get("etfs", [])),
        "marketScopeEtfCount": market.get("etfCount"),
        "marketScope5dCount": market.get("etfCount5d"),
        "marketScope20dCount": market.get("etfCount20d"),
        "marketScopeSource": "同花顺精确交易日基金类型/NAV + 沪深交易所日终份额",
        "classifiedCoverageOfMarketPct": round(
            len(snapshot.get("etfs", [])) / max(int(market.get("etfCount") or 1), 1) * 100, 2
        ),
        "marketScopeReconciliation": {
            "aShareEquityShareFlow1d": market.get("flow1d"),
            "classifiedGroupShareFlow1d": classified_flow,
            "ungroupedDifference": round(float(market.get("flow1d") or 0) - classified_flow, 2),
        },
    })

    snapshot["schemaVersion"] = 6
    snapshot.setdefault("methodology", {}).update({
        "flow": "ETF份额净流入/净流出 =（T日交易所日终份额 − T-1日公司行动调整后的可比份额）× T日单位净值。T-1只作为计算T日份额变化的基准；该结果就是T日份额资金变化。",
        "metricSeparation": "网站同时展示两套独立口径：当日交易净流入/净流出按同日成交额和外盘/内盘主动成交方向估算，只显示买卖差额；ETF份额净流入/净流出按日终份额变化×T日NAV计算。原主力净流入字段仅作辅助，不再作为首页第一层指标。",
        "multiDay": "5日/20日当前字段为端点份额变化×期末单位净值，字段明确标记 Endpoint；不是逐日净流入额之和。schema v6开始落盘每日单ETF份额flow1d，积累足够交易日后再生成真正5日/20日累计净流入额。",
        "scope": "首页两套指标都固定使用A股股票ETF范围，不含跨境股票ETF、债券ETF、货币ETF和商品ETF；同时保留全部ETF、股票ETF（含跨境）和六类资产范围用于审计与对照。",
        "valuation": "ETF份额净流入/净流出主口径使用同日单位净值；flowMetrics.primaryMarket.valuationComparisons 同时保存同一份额变化按成交均价估值的对照总额。",
        "coordinates": "横轴 = 20日相对沪深300收益率；纵轴 = 5日端点份额变化×期末NAV ÷ 5日前参考规模（%）。",
    })

    _regenerate_v2_conclusion(snapshot)


def _v2_postprocess(snapshot: dict[str, Any], day: date) -> None:
    _ORIG_POSTPROCESS(snapshot, day)
    ths = production._get_ths_day(day)
    apply_v2_semantics(snapshot, day, production._LAST_WINDOW, ths, _load_secondary_spot(day))


def daily_flow_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    primary = snapshot.get("flowMetrics", {}).get("primaryMarket", {})
    return {
        "schemaVersion": 1,
        "tradeDate": snapshot["tradeDate"],
        "generatedAt": snapshot["generatedAt"],
        "metric": "primaryMarketNetSubscriptionEstimate",
        "valuation": "sameDayUnitNAV",
        "marketScopes": primary.get("scopeTotals", {}),
        "assetClassTotals": primary.get("assetClassTotals", {}),
        "valuationComparisons": primary.get("valuationComparisons", {}),
        "etfs": [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "groupId": item.get("groupId"),
                "shares": item.get("shares"),
                "previousComparableShares": item.get("previousComparableShares"),
                "shareDelta1d": item.get("shareDelta1d"),
                "nav": item.get("nav"),
                "flow1d": item.get("flow1d"),
                "flow1dAvgPriceEstimate": item.get("flow1dAvgPriceEstimate"),
            }
            for item in snapshot.get("etfs", [])
        ],
    }


def _v2_atomic_publish(snapshot: dict[str, Any]) -> Path:
    path = _ORIG_ATOMIC_PUBLISH(snapshot)
    daily_dir = base.PUBLIC / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(daily_flow_payload(snapshot), ensure_ascii=False, indent=2)
    (daily_dir / f'{snapshot["tradeDate"]}.json').write_text(text, "utf-8")
    (daily_dir / "latest.json").write_text(text, "utf-8")
    return path


def install_v2_pipeline() -> None:
    production._postprocess_snapshot = _v2_postprocess
    production.install_production_pipeline()
    base.atomic_publish = _v2_atomic_publish


def main() -> int:
    install_v2_pipeline()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
