"""Re-render ONLY the headline of the current verified snapshot, without fetching data.

Usage: python data_pipeline/refresh_conclusion_copy.py [--write]
Audits both files, checks their exact original agreement, and refuses numeric or
non-headline changes. Without --write this is a read-only release rehearsal.
"""
import argparse
import copy
import json
from pathlib import Path

from audit_snapshot_v6 import audit
from update_daily_v2 import _regenerate_v2_conclusion


def refresh(write=False):
    root = Path(__file__).resolve().parents[1] / "site/data"
    latest = root / "latest.json"
    original = json.loads(latest.read_text("utf-8"))
    historical = root / "history" / f"{original['tradeDate']}.json"
    if original != json.loads(historical.read_text("utf-8")):
        raise ValueError("latest and same-date history disagree; do not overwrite")
    # The auditor resolves frozen order_flow relative to site/data. History is
    # byte-equivalent as an object and must not be audited with the wrong base.
    audit(latest)
    regenerated = copy.deepcopy(original)
    _regenerate_v2_conclusion(regenerated)
    candidate = copy.deepcopy(original)
    candidate["conclusion"]["headline"] = regenerated["conclusion"]["headline"]
    # Regeneration itself must not have changed canonical source facts.
    if any(regenerated[key] != value for key, value in original.items() if key != "conclusion"):
        raise ValueError("conclusion regeneration changed source facts")
    serialized = json.dumps(candidate, ensure_ascii=False, indent=2) + "\n"
    temporary = latest.with_suffix(".copy-check.json")
    try:
        temporary.write_text(serialized, "utf-8")
        audit(temporary)
        if write:
            # Local pair enters production in a single Git tree/commit.
            historical.write_text(serialized, "utf-8")
            temporary.replace(latest)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(candidate["conclusion"]["headline"])
    print("Only headline updated; dates, NAV, shares, flows, counts and coverage unchanged." if write else "Read-only release rehearsal passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    refresh(parser.parse_args().write)
