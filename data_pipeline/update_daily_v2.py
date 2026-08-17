"""Single production entrypoint for ETF Flow Radar schema v6.

This entrypoint keeps the battle-tested exchange/NAV transport and corporate-
action guards, then applies ``flow_model_v2`` as the *only* client-facing flow
semantics layer. New code and documentation should invoke this file rather than
calling the older intermediate wrappers directly.
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
    """Prefer the immutable post-close snapshot for the target trade date.

    Current ETF spot endpoints roll forward with the natural date, so an
    overnight rebuild must not infer Friday order flow from a Monday snapshot.
    The dedicated post-close collector persists the exact trading-day values.
    """
    path = base.PUBLIC / "order_flow" / f"{day.isoformat()}.json"
    if path.exists():
        payload = json.loads(path.read_text("utf-8"))
        if payload.get("tradeDate") == day.isoformat() and payload.get("metric") == "secondaryMarketMainOrderFlow":
            rows = payload.get("etfs", [])
            if rows:
                frame = pd.DataFrame(rows)
                frame["代码"] = frame["code"].astype(str).str.zfill(6)
                frame["名称"] = frame["name"].astype(str)
                frame["主力净流入-净额"] = pd.to_numeric(frame["mainOrderFlow1d"], errors="coerce") * 1e8
                frame["数据日期"] = day.isoformat()
                return frame[["代码", "名称", "主力净流入-净额", "数据日期"]]
    # Fallback is deliberately safe: flow_model_v2 accepts this snapshot only if
    # its own provider date exactly matches `day`; otherwise it records unavailable.
    return guarded._get_spot()


def _v2_postprocess(snapshot: dict[str, Any], day: date) -> None:
    # Preserve mature quality gates, SW parent mapping and issue collection first.
    _ORIG_POSTPROCESS(snapshot, day)

    # Then replace every client-facing flow number with the explicit v2 model.
    ths = production._get_ths_day(day)
    spot = _load_secondary_spot(day)
    flow_model_v2.apply_flow_model(snapshot, day, production._LAST_WINDOW, ths, spot)
    flow_comparison_v2.add_primary_valuation_comparisons(snapshot)
    flow_scope_breakdown_v2.add_asset_class_totals(snapshot)

    # Parent rollups must be rebuilt after leaf ETF flows switch to the canonical
    # NAV-valued primary-market metric.
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
        "marketScopeEtfCount": market.get("etfCount"),
        "marketScope5dCount": market.get("etfCount5d"),
        "marketScope20dCount": market.get("etfCount20d"),
        "marketScopeSource": "同花顺精确交易日基金类型/NAV + 沪深交易所日终份额",
        "classifiedCoverageOfMarketPct": round(len(snapshot.get("etfs", [])) / max(int(market.get("etfCount") or 1), 1) * 100, 2),
        "marketScopeReconciliation": {
            "aShareEquityPrimaryFlow1d": market.get("flow1d"),
            "classifiedGroupPrimaryFlow1d": classified_flow,
            "ungroupedDifference": round(float(market.get("flow1d") or 0) - classified_flow, 2),
        },
    })

    snapshot["schemaVersion"] = 6
    snapshot.setdefault("methodology", {}).update({
        "flow": "一级市场净申购/赎回估算 =（T日交易所日终份额 − T-1日公司行动调整后的可比份额）× T日单位净值。单位净值是主展示估值口径；同一份额变化再乘成交均价的结果单独保留用于Wind/资讯口径对照，不再混入主字段。",
        "metricSeparation": "一级市场净申购/赎回与二级市场主力净流入是两个不同变量。二级市场主力资金由收盘后轻量任务在交易日当天留存，并单独记录于 flowMetrics.secondaryMarketOrderFlow，绝不覆盖一级市场数据；如果没有同日留存，历史回放直接标记 unavailable。",
        "multiDay": "5日/20日当前字段为端点份额变化×期末单位净值，字段明确标记 Endpoint；不是逐日净申购额之和。schema v6开始落盘每日单ETF一级市场flow1d，积累足够交易日后再生成真正5日/20日累计净申购额。",
        "scope": "同时保存全部ETF、股票ETF（含跨境）和A股股票ETF三个一级市场比较口径，并额外保存A股股票、跨境、债券、货币、商品、其他六个互斥资产类别，使分类加总严格回到全部ETF。网站主口径仍是A股股票ETF；与Wind/Choice/iFinD或资讯报道对比时必须先匹配统计范围。",
        "valuation": "主口径使用同日单位净值；flowMetrics.primaryMarket.valuationComparisons 同时保存同一份额变化按成交均价估值的对照总额。二者是估值方法差异，不是两个独立资金事件。",
    })
    snapshot["methodology"]["coordinates"] = "横轴 = 20日相对沪深300收益率；纵轴 = 5日端点份额变化×期末NAV ÷ 5日前参考规模（%）。"

    production._regenerate_conclusion(snapshot)
    headline = snapshot.get("conclusion", {}).get("headline")
    if isinstance(headline, str) and "当日合计" in headline:
        snapshot["conclusion"]["headline"] = headline.replace("当日合计", "当日一级市场净申购/赎回估算合计", 1)


def _daily_flow_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
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
    payload = _daily_flow_payload(snapshot)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
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
