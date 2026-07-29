from __future__ import annotations

import json
from pathlib import Path

from .constants import PRODUCT, SPECIALISTS, VERSION
from .fsutil import atomic_write_json, backup_path, ensure_dir, sha256_file, utc_stamp
from .paths import InstallPaths


def snapshot_targets(paths: InstallPaths) -> dict[str, Path]:
    targets = {
        "hermes-config.yaml": paths.hermes_home / "config.yaml",
        "hermes-env": paths.hermes_home / ".env",
        "gooros-customer.yaml": paths.customer_config,
        "gooros-secrets.env": paths.secrets_env,
        "board.db": paths.project_dir / "board.db",
        "agent-logs.db": paths.project_dir / "agent-logs.db",
        "content": paths.project_dir / "content",
        "server.py": paths.project_dir / "server.py",
        "template.html": paths.project_dir / "template.html",
        "index.html": paths.project_dir / "index.html",
        "orchestrator-rules.md": paths.hermes_home / "GOOROS_ORCHESTRATOR.md",
        "telegram-topic-profiles": paths.hermes_home / "plugins" / "telegram_topic_profiles",
        "shared-scripts": paths.hermes_home / "agents" / "_shared",
        "install-state.json": paths.install_state,
        "ownership.json": paths.ownership_map,
        "migrations.db": paths.project_dir / ".gooros" / "migrations.db",
    }
    for agent in SPECIALISTS:
        targets[f"profile-{agent}-SOUL.md"] = paths.hermes_home / "profiles" / agent / "SOUL.md"
        targets[f"profile-{agent}-config.yaml"] = paths.hermes_home / "profiles" / agent / "config.yaml"
    return targets


def create_snapshot(paths: InstallPaths, label: str = "install") -> Path:
    snap = paths.snapshot_dir / f"{utc_stamp()}-{label}"
    ensure_dir(snap)
    targets = snapshot_targets(paths)
    manifest: dict[str, object] = {
        "product": PRODUCT,
        "version": VERSION,
        "label": label,
        "created_at": utc_stamp(),
        "files": {},
    }
    for name, src in targets.items():
        dst = snap / name
        entry: dict[str, object] = {"source": str(src), "present": src.exists()}
        if src.exists():
            backup_path(src, dst)
            if dst.exists() and dst.is_file():
                entry.update({"type": "file", "sha256": sha256_file(dst)})
            elif dst.exists():
                entry.update({"type": "directory"})
        manifest["files"][name] = entry
    atomic_write_json(snap / "snapshot.json", manifest, mode=0o600)
    atomic_write_json(paths.snapshot_dir / "last-snapshot.json", {"path": str(snap), "label": label}, mode=0o600)
    return snap


def write_install_state(paths: InstallPaths, extra: dict[str, object] | None = None) -> None:
    existing = {}
    if paths.install_state.exists():
        try:
            existing = json.loads(paths.install_state.read_text(encoding="utf-8-sig"))
        except Exception:
            existing = {}
    data: dict[str, object] = {
        "product": PRODUCT,
        "version": VERSION,
        "schema_version": 1,
        "installed_at": existing.get("installed_at") or utc_stamp(),
    }
    if extra:
        data.update(extra)
    atomic_write_json(paths.install_state, data, mode=0o600)
