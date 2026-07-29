"""Remove dashboard seed tasks from early public releases.

The task board is customer-owned. This migration deletes only the exact demo
rows created by versions before 0.1.7, preserving any edited or user-created
tasks.
"""

from __future__ import annotations

import sqlite3


SEED_TASKS = {
    "seed-outline-script": "Outline next week's video script",
    "seed-sponsor-emails": "Reply to sponsor and collab emails",
    "seed-edit-video": "Edit this week's YouTube video",
    "seed-launch-newsletter": "Write the launch-day newsletter",
    "seed-publish-blog": "Publish the new blog post",
    "seed-social-posts": "Schedule this week's social posts",
}


def apply(paths, runner) -> None:
    db = paths.project_dir / "board.db"
    if not db.exists():
        runner.log("[migration] 0002: board.db missing; nothing to clean")
        return
    removed = 0
    with sqlite3.connect(db) as conn:
        for task_id, title in SEED_TASKS.items():
            removed += conn.execute(
                "DELETE FROM tasks WHERE id = ? AND title = ? AND COALESCE(notes, '') = ''",
                (task_id, title),
            ).rowcount
    runner.log(f"[migration] 0002: removed {removed} dashboard seed task(s)")
