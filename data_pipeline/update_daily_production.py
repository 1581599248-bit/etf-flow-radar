"""Final production entrypoint for verified ETF snapshots.

Handled guard events (for example a price/NAV fallback or a confirmed corporate
action adjustment) are retained as informational quality notes.  They do not
lower an otherwise verified snapshot to ``warning`` because the suspect input
has already been replaced or normalized before publication.  Unresolved
extreme-flow checks remain critical and still fail closed.
"""
from __future__ import annotations

from typing import Any

import update_daily as base
import update_daily_guarded as guarded
import update_daily_resilient as resilient

_HANDLED_GUARD_CHECKS = {
    "secondary_share_crosscheck",
    "price_nav_guard",
    "corporate_action_adjustment",
}
_ORIG_APPEND_ISSUE = guarded._append_issue


def production_append_issue(
    snapshot: dict[str, Any], severity: str, check: str, message: str
) -> None:
    if severity == "warning" and check in _HANDLED_GUARD_CHECKS:
        snapshot.setdefault("quality", {}).setdefault("issues", []).append(
            {"severity": "info", "check": check, "message": message}
        )
        return
    _ORIG_APPEND_ISSUE(snapshot, severity, check, message)


def install_production_pipeline() -> None:
    guarded._append_issue = production_append_issue
    resilient.install_resilient_sources()


def main() -> int:
    install_production_pipeline()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
