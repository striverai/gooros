"""Initial Gooros Hermes install migration.

The first public release initializes state through the installer itself, so the
upgrade migration is intentionally a no-op. Keeping an explicit function lets
the updater record this baseline in the migration ledger.
"""

from __future__ import annotations


def apply(paths, runner) -> None:
    runner.log("[migration] 0001_initial baseline; no schema changes")
