"""Conservative research taxonomy before Data Contract 7.0 classification audit.

Legacy name rules sometimes forced a broad ETF theme into a narrower SW-style
industry (e.g. 消费 -> 食品饮料, 新能源 -> 电力设备). This layer preserves the
useful research signal without making that false precision claim: broad names
are remapped to equally broad, explicitly labelled research themes.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


_RULES = [
    {
        "id": "theme_consumer",
        "name": "消费",
        "source": "sw_food_beverage",
        "trigger": re.compile(r"消费", re.I),
        "specific": re.compile(r"食品|饮料|白酒|啤酒|乳业|乳品|调味|酒ETF|食品饮料", re.I),
        "reason": "broad consumer ETF is kept as a consumer research theme instead of being asserted as food-and-beverage",
    },
    {
        "id": "theme_new_materials",
        "name": "新材料",
        "source": "sw_basic_chemicals",
        "trigger": re.compile(r"新材料|新材|材料ETF", re.I),
        "specific": re.compile(r"化工|化学|化纤|基础化工", re.I),
        "reason": "broad materials ETF is kept as a new-materials research theme instead of being asserted as basic chemicals",
    },
    {
        "id": "theme_energy",
        "name": "能源",
        "source": "sw_petrochemical",
        "trigger": re.compile(r"能源", re.I),
        "specific": re.compile(r"石油|石化|油气|原油", re.I),
        "reason": "broad energy ETF is kept as an energy research theme instead of being asserted as petrochemicals",
    },
    {
        "id": "theme_home_furnishing",
        "name": "家居",
        "source": "sw_home_appliances",
        "trigger": re.compile(r"家居", re.I),
        "specific": re.compile(r"家电|家用电器", re.I),
        "reason": "home-furnishing ETF is not asserted as the home-appliance industry",
    },
    {
        "id": "theme_advanced_manufacturing",
        "name": "高端制造",
        "source": "sw_machinery",
        "trigger": re.compile(r"高端制造|智能制造|高端装备|制造ETF|装备ETF", re.I),
        "specific": re.compile(r"机械|机器人|工业母机|机床|自动化设备", re.I),
        "reason": "broad manufacturing ETF is kept as an advanced-manufacturing research theme instead of being asserted as machinery",
    },
    {
        "id": "theme_discretionary_consumer",
        "name": "可选消费",
        "source": "sw_retail",
        "trigger": re.compile(r"可选消费|线上消费|在线消费", re.I),
        "specific": re.compile(r"零售|商贸|电商|互联网电商", re.I),
        "reason": "broad discretionary/digital consumer ETF is not asserted as retail",
    },
    {
        "id": "theme_new_energy",
        "name": "新能源",
        "source": "sw_power_equipment",
        "trigger": re.compile(r"新能源|新能ETF", re.I),
        "specific": re.compile(r"光伏|电池|锂电|储能|风电|电网|电力设备|新能源车|新能车", re.I),
        "reason": "broad new-energy ETF is kept as a new-energy research theme instead of being asserted as power equipment",
    },
    {
        "id": "theme_mining",
        "name": "矿业",
        "source": "sw_nonferrous",
        "trigger": re.compile(r"矿业", re.I),
        "specific": re.compile(r"有色|稀土|稀有金属|工业金属|贵金属|黄金", re.I),
        "reason": "generic mining ETF is kept as a mining research theme instead of being asserted as nonferrous metals",
    },
]


def _match(row: dict[str, Any]) -> dict[str, Any] | None:
    gid = str(row.get("groupId") or "")
    name = str(row.get("name") or "")
    for rule in _RULES:
        if gid != rule["source"]:
            continue
        if rule["trigger"].search(name) and not rule["specific"].search(name):
            return rule
    return None


def apply(snapshot: dict[str, Any]) -> None:
    """Remap only broad-name false-precision cases; never alter market scope."""
    remapped: list[dict[str, Any]] = []
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rule_by_id = {rule["id"]: rule for rule in _RULES}

    for row in snapshot.get("etfs", []):
        rule = _match(row)
        if rule:
            previous = str(row.get("groupId") or "")
            row["groupId"] = rule["id"]
            row["groupName"] = rule["name"]
            row["kind"] = "industry"
            row["taxonomyRuleId"] = rule["id"]
            row["taxonomyReason"] = rule["reason"]
            remapped.append({
                "code": str(row.get("code", "")).zfill(6),
                "name": str(row.get("name") or ""),
                "fromGroupId": previous,
                "toGroupId": rule["id"],
                "toGroupName": rule["name"],
                "reason": rule["reason"],
            })
        gid = str(row.get("groupId") or "")
        if gid:
            members[gid].append(row)

    if not remapped:
        snapshot.setdefault("quality", {})["broadThemeTaxonomyRemaps"] = []
        return

    remap_by_code = {item["code"]: item for item in remapped}
    for row in snapshot.get("universe", []):
        code = str(row.get("code", "")).zfill(6)
        item = remap_by_code.get(code)
        if not item:
            continue
        row["groupId"] = item["toGroupId"]
        row["groupName"] = item["toGroupName"]
        row["kind"] = "industry"
        row["taxonomyRuleId"] = item["toGroupId"]
        row["taxonomyReason"] = item["reason"]

    existing = {str(group.get("id") or ""): group for group in snapshot.get("groups", [])}
    for gid, rows in members.items():
        if gid not in rule_by_id or gid in existing:
            continue
        rule = rule_by_id[gid]
        representative = max(rows, key=lambda row: float(row.get("aum") or 0))
        existing[gid] = {
            "id": gid,
            "name": rule["name"],
            "kind": "industry",
            "etfCount": len(rows),
            "representative": {
                "code": str(representative.get("code", "")).zfill(6),
                "name": str(representative.get("name") or ""),
            },
            "return1d": None,
            "return5d": None,
            "return20d": None,
            "relativeReturn20d": None,
            "priceFlowState": "数据待补",
            "taxonomyRuleId": gid,
            "taxonomyReason": rule["reason"],
        }

    snapshot["groups"] = list(existing.values())
    quality = snapshot.setdefault("quality", {})
    quality["broadThemeTaxonomyRemaps"] = remapped
    quality["broadThemeTaxonomyRemapCount"] = len(remapped)
    quality["researchTaxonomyPolicy"] = (
        "broad ETF names use equally broad research themes; they are not forced into narrower SW-style industries"
    )
