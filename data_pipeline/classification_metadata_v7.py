"""Classification provenance and compatibility fingerprint for Contract 7.0."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SOURCES = (
    ROOT / "classification.json",
    ROOT / "research_taxonomy_v7.py",
    ROOT / "system_contract_v7.py",
)


def digest() -> str:
    hasher = hashlib.sha256()
    hasher.update(b"ETF_FLOW_RADAR_CLASSIFICATION_CONTRACT_7\0")
    for path in SOURCES:
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def apply(snapshot: dict[str, Any]) -> None:
    value = digest()
    snapshot["classificationRuleDigest"] = value
    quality = snapshot.setdefault("quality", {})
    quality["classificationRuleDigest"] = value
    quality["classificationRuleDigestSources"] = [path.name for path in SOURCES]
    quality["classificationRuleDigestAlgorithm"] = "sha256_ordered_source_bytes"

    for row in snapshot.get("universe", []):
        if row.get("classificationStatus") == "ambiguous":
            row["classificationMethod"] = "ambiguous_name_rule_excluded_from_research_groups"
        elif row.get("taxonomyRuleId"):
            row["classificationMethod"] = "broad_name_research_theme"
        elif row.get("groupId"):
            row["classificationMethod"] = "fund_name_research_rule"

    for row in snapshot.get("etfs", []):
        row["classificationMethod"] = (
            "broad_name_research_theme" if row.get("taxonomyRuleId") else "fund_name_research_rule"
        )
    for group in snapshot.get("groups", []):
        group["classificationMethod"] = (
            "broad_name_research_theme" if group.get("taxonomyRuleId") else "fund_name_research_group"
        )
