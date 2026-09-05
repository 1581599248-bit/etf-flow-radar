"""Concise, evidence-limited market interpretation (not an investor-intent model).

Rank only mutually exclusive broad/style/industry leaf groups. Directions come
from their leaders; strength/dispersion is checked against ALL eligible groups.
Positive/negative sums are sums of group NET estimates, never gross creations
or redemptions. Trade flow and share flow are never added or netted together.
"""
from __future__ import annotations

import math

KINDS = {"broad", "style", "industry"}
MIN_LEADER_SHARE = 0.50
SMALL_SIDE_SHARE = 0.05
SMALL_SIDE_AUM = 0.00005  # 0.005% of market AUM; description only, never rank filtering.


def direction(group):
    name = group["name"]
    if any(x in name for x in ("红利", "股息")):
        return "高股息"
    if group["kind"] == "broad":
        if any(x in name for x in ("科创", "创业", "双创")):
            return "科技成长"
        if any(x in name for x in ("中证500", "中证1000", "中证2000", "国证2000")):
            return "中小盘"
        if any(x in name for x in ("沪深300", "中证A500", "中证A50", "上证50", "中证A100", "上证180")):
            return "大盘宽基"
        return "其他宽基"
    # Growth style is wider than technology; never relabel it as technology.
    if name == "成长":
        return "成长风格"
    for label, keywords in (
        ("金融", ("银行", "券商", "证券", "保险", "金融")),
        ("科技成长", ("科技", "半导体", "芯片", "算力", "人工智能", "机器人", "电子", "计算机", "通信", "软件", "信创", "互联网", "传媒", "游戏")),
        ("医药医疗", ("医药", "创新药", "中药", "医疗", "生物")),
        ("新能源", ("新能源", "光伏", "锂电", "储能", "电力设备", "碳中和")),
        ("资源周期", ("有色", "稀土", "煤炭", "石油", "钢铁", "化工", "建材", "建筑材料", "黄金")),
        ("消费", ("消费", "食品", "白酒", "家用电器", "零售", "社会服务", "农林牧渔", "养殖")),
        ("制造军工", ("军工", "卫星", "机械", "汽车", "驾驶")),
        ("地产基建", ("房地产", "建筑装饰")),
        ("公用运输", ("公用事业", "交通运输", "环保")),
        ("价值质量", ("价值", "质量", "现金流", "低波")),
    ):
        if any(x in name for x in keywords):
            return label
    return "其他风格" if group["kind"] == "style" else "其他行业"


def eligible_groups(groups):
    """Fail closed on invalid/duplicate leaf data; do not silently shrink the pool."""
    result, seen = [], set()
    for group in groups:
        if group.get("kind") not in KINDS:
            continue
        name, value = group.get("name"), group.get("flow1d")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("conclusion group name is missing")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"conclusion flow is invalid: {name}")
        identity = group.get("id") or (group["kind"], name)
        if identity in seen:
            raise ValueError(f"duplicate conclusion group: {identity}")
        seen.add(identity)
        result.append(group)
    return result


def side_context(groups, sign, aum=None):
    rows = sorted((g for g in groups if sign * g["flow1d"] > 0),
                  key=lambda g: (-sign * g["flow1d"], g["name"], g["kind"]))
    total = sum(abs(g["flow1d"]) for g in rows)
    all_absolute = sum(abs(g["flow1d"]) for g in groups)
    labels = list(dict.fromkeys(direction(g) for g in rows[:2]))
    # Assess the full magnitude of these directions, not just two winning rows.
    represented = sum(abs(g["flow1d"]) for g in rows if direction(g) in labels)
    floor = max(0.1, aum * SMALL_SIDE_AUM) if aum and math.isfinite(aum) and aum > 0 else 0.1
    return {
        "labels": labels, "total": total,
        "small": total > 0 and total < floor and total < all_absolute * SMALL_SIDE_SHARE,
        "focused": total > 0 and represented / total >= MIN_LEADER_SHARE,
    }


def market_state(primary_value, primary_strength, trade_value, trade_strength):
    p = 0 if primary_strength == "flat" or primary_value == 0 else (1 if primary_value > 0 else -1)
    if trade_value is None:
        return "市场风向暂缺交易端确认"
    t = 0 if trade_strength == "balanced" or trade_value == 0 else (1 if trade_value > 0 else -1)
    return {
        (1, 1): "市场资金偏向增配",
        (-1, -1): "市场资金偏向减配",
        (1, -1): "市场资金流向分化",
        (-1, 1): "市场资金流向分化",
        (1, 0): "市场配置端偏向增配",
        (-1, 0): "市场配置端偏向减配",
        (0, 1): "市场偏交易性买入",
        (0, -1): "市场偏交易性卖出",
        (0, 0): "市场资金方向暂不明朗",
    }[p, t]


def render_market(primary_value, primary_strength, trade_value, trade_strength, groups, aum=None):
    state = market_state(primary_value, primary_strength, trade_value, trade_strength)
    if groups is None:
        return f"{state}，配置方向数据暂缺。"
    rows = eligible_groups(groups)
    if not rows:
        return f"{state}，配置方向数据暂缺。"
    incoming, outgoing = side_context(rows, 1, aum), side_context(rows, -1, aum)
    if not incoming["total"] and not outgoing["total"]:
        return f"{state}，各方向份额净变动接近零。"
    if (incoming["labels"] and set(incoming["labels"]) == set(outgoing["labels"])
            and incoming["focused"] and outgoing["focused"]
            and not incoming["small"] and not outgoing["small"]):
        return f"{state}，{'与'.join(incoming['labels'])}内部申赎分化。"
    parts = [state]
    if incoming["total"]:
        label = "与".join(incoming["labels"])
        if incoming["small"]:
            parts.append(f"少量申购偏向{label}")
        elif not incoming["focused"]:
            parts.append("申购分布于多个方向")
        elif primary_value < 0 and primary_strength != "flat":
            parts.append(f"局部申购偏向{label}")
        else:
            parts.append(f"配置偏向{label}")
    else:
        parts.append("未见净申购方向")
    if outgoing["total"]:
        label = "与".join(outgoing["labels"])
        if outgoing["small"]:
            parts.append(f"部分{label}方向小额流出")
        else:
            # "部分" names observed leader directions, not a claim that they
            # dominate all redemptions or that the whole category is exiting.
            parts.append(f"部分{label}方向资金流出")
    else:
        parts.append("未见净赎回方向")
    return "，".join(parts) + "。"
