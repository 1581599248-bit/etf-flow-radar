"""Objective client conclusion wording for Data Contract 7.0.

The conclusion contains only facts directly supported by the current snapshot.
It deliberately avoids subjective confidence grades, causal inference and
investment interpretation.
"""
from __future__ import annotations

from typing import Any


def _yuan(row: dict[str, Any]) -> float:
    value = row.get("primaryFlow1dYuanEstimate")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _amount_yi(yuan: float) -> float:
    return abs(yuan) / 1e8


def _market_phrase(yuan: float) -> str:
    if yuan > 0:
        return f"净申购金额估算为{_amount_yi(yuan):.1f}亿元"
    if yuan < 0:
        return f"净赎回金额估算为{_amount_yi(yuan):.1f}亿元"
    return "净申购/赎回金额估算为0.0亿元"


def _group_phrase(group: dict[str, Any]) -> str:
    yuan = _yuan(group)
    if yuan > 0:
        return f"{group['name']}净申购金额估算{_amount_yi(yuan):.1f}亿元"
    if yuan < 0:
        return f"{group['name']}净赎回金额估算{_amount_yi(yuan):.1f}亿元"
    return f"{group['name']}净申购/赎回金额估算0.0亿元"


def _product_phrase(item: dict[str, Any], positive: bool) -> str:
    yuan = float(item.get("amountYuanEstimate") or 0)
    label = "净申购金额估算" if positive else "净赎回金额估算"
    return f"{item.get('name')}{label}{_amount_yi(yuan):.1f}亿元"


def rebuild(snapshot: dict[str, Any]) -> None:
    market = snapshot.get("market", {})
    market_yuan = float(market.get("primaryFlow1dYuanEstimate") or 0)
    groups = snapshot.get("groups", [])
    broad = [group for group in groups if group.get("kind") == "broad"]
    sectors = [group for group in groups if group.get("kind") == "industry"]

    broad_sub = [group for group in broad if _yuan(group) > 0]
    broad_red = [group for group in broad if _yuan(group) < 0]
    broad_zero = [group for group in broad if _yuan(group) == 0]
    headline = (
        "依据交易所日终份额变化与同日单位净值，"
        f"A股股票ETF一级市场当日{_market_phrase(market_yuan)}；"
        f"统计范围为{int(market.get('etfCount') or 0)}只A股股票ETF。"
        f"宽基研究组中{len(broad_sub)}个净申购、{len(broad_red)}个净赎回"
        + (f"、{len(broad_zero)}个估算金额为0" if broad_zero else "")
        + "。"
    )

    facts: list[str] = []
    if broad:
        if broad_red:
            ordered = sorted(broad_red, key=_yuan)
            facts.append("宽基净赎回金额估算居前为" + "、".join(_group_phrase(group) for group in ordered[:3]) + "。")
        else:
            facts.append("宽基研究组当日未录得净赎回金额估算。")

    sector_sub = [group for group in sectors if _yuan(group) > 0]
    sector_red = [group for group in sectors if _yuan(group) < 0]
    sector_zero = [group for group in sectors if _yuan(group) == 0]
    if sectors:
        if sector_sub:
            leaders = sorted(sector_sub, key=_yuan, reverse=True)[:2]
            sentence = "行业/主题研究分组净申购金额估算居前为" + "、".join(_group_phrase(group) for group in leaders)
            if sector_red:
                worst = min(sector_red, key=_yuan)
                sentence += f"；净赎回金额估算最多为{_group_phrase(worst)}。"
            else:
                sentence += "；当日未录得净赎回研究分组。"
            facts.append(sentence)
        elif sector_red:
            worst = min(sector_red, key=_yuan)
            facts.append(f"行业/主题研究分组当日未录得净申购金额估算；净赎回金额估算最多为{_group_phrase(worst)}。")
        elif sector_zero:
            facts.append("行业/主题研究分组当日净申购/赎回金额估算均为0。")

    largest_sub = market.get("largestNetSubscriptionEtf")
    largest_red = market.get("largestNetRedemptionEtf")
    product_parts: list[str] = []
    if isinstance(largest_sub, dict):
        product_parts.append(_product_phrase(largest_sub, True))
    if isinstance(largest_red, dict):
        product_parts.append(_product_phrase(largest_red, False))
    if product_parts:
        facts.append("单只ETF中，" + "；".join(product_parts) + "。")

    status = str(snapshot.get("status") or "")
    data_status = "已验证" if status == "verified" else "已验证（有提示）" if status == "warning" else "不可发布"
    coverage = float(snapshot.get("quality", {}).get("classifiedCoverageOfMarketPct") or 0)
    ambiguous = int(snapshot.get("quality", {}).get("ambiguousClassificationCount") or 0)
    snapshot["conclusion"] = {
        "headline": headline,
        "facts": facts,
        "interpretation": (
            "以上仅描述ETF一级市场份额净申购/赎回金额估算及研究分组分布。"
            "二级市场主动成交方向统计与一级市场净申购/赎回是不同经济变量；"
            "上述数据不直接代表投资者最终持仓意图、未来价格方向或投资建议。"
        ),
        "dataStatus": data_status,
        "dataStatusNote": (
            f"研究分组覆盖A股股票ETF市场范围的{coverage:.2f}%；"
            f"当前有{ambiguous}只名称存在歧义的ETF保留在市场总量、但不进入研究分组结论。"
            "最终发布仍须通过语义一致性、金额精度、多日累计精度及客户端测试。"
        ),
    }
