"""Run the data-integrity health check (issue #79) and print a report.

Usage:
    python scripts/data_integrity_check.py

Reads DATABASE_URL the same way deeptutor.services.db.engine does. Exits
with status 1 if any findings were reported (so this is safe to run on a
schedule / in CI and alert on a nonzero exit code), 0 if everything is
consistent.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deeptutor.multi_user.health_check import run_all_checks  # noqa: E402


async def main() -> int:
    findings = await run_all_checks()
    if not findings:
        print("Data integrity check: no issues found.")
        return 0

    print(f"Data integrity check: {len(findings)} issue(s) found.\n")
    by_check: dict[str, list[dict]] = {}
    for finding in findings:
        by_check.setdefault(finding["check"], []).append(finding)

    for check_name, items in by_check.items():
        print(f"[{check_name}] ({len(items)})")
        for item in items:
            ids = ", ".join(
                f"{k}={v}" for k, v in item.items() if k not in ("check", "detail")
            )
            print(f"  - {item['detail']} ({ids})")
        print()

    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
