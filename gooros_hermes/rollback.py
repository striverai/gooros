from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .fsutil import atomic_write_json, ensure_dir, read_json, utc_stamp
from .paths import InstallPaths, default_paths
from .runner import Runner
from .verify import verify_install

CUSTOMER_DATA_KEYS = {"board.db", "agent-logs.db", "content"}
DELETE_IF_MISSING_KEYS = {
    "server.py",
    "template.html",
    "index.html",
    "orchestrator-rules.md",
    "telegram-topic-profiles",
    "shared-scripts",
    "install-state.json",
    "ownership.json",
    "migrations.db",
}


def _is_inside(path: Path, root: Path) -> bool:
    try:
        return path.expanduser().resolve().is_relative_to(root.expanduser().resolve())
    except OSError:
        return False


def _allowed_restore_path(paths: InstallPaths, dst: Path) -> bool:
    return any(_is_inside(dst, root) for root in (paths.hermes_home, paths.project_dir, paths.config_dir, paths.data_dir))


def latest_snapshot(paths: InstallPaths) -> Path:
    pointer = read_json(paths.snapshot_dir / "last-snapshot.json", default={})
    if isinstance(pointer, dict) and pointer.get("path"):
        candidate = Path(str(pointer["path"])).expanduser()
        if (candidate / "snapshot.json").exists():
            return candidate
    snapshots = sorted(p for p in paths.snapshot_dir.glob("*") if (p / "snapshot.json").exists())
    if not snapshots:
        raise RuntimeError(f"no snapshots found in {paths.snapshot_dir}")
    return snapshots[-1]


def _restore_backup(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    if src.is_dir():
        if dst.exists():
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        shutil.copytree(src, dst, symlinks=True)
        return
    if dst.exists() and dst.is_dir() and not dst.is_symlink():
        shutil.rmtree(dst)
    shutil.copy2(src, dst)


def _remove_release_owned_path(dst: Path) -> None:
    if not dst.exists():
        return
    if dst.is_dir() and not dst.is_symlink():
        shutil.rmtree(dst)
    else:
        dst.unlink()


def restore_snapshot(
    paths: InstallPaths,
    snapshot: Path,
    runner: Runner,
    *,
    restore_customer_data: bool,
    verify_public: bool = False,
    verify_after: bool = True,
) -> list[str]:
    manifest = read_json(snapshot / "snapshot.json")
    if not isinstance(manifest, dict):
        raise RuntimeError(f"invalid snapshot manifest: {snapshot / 'snapshot.json'}")
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        raise RuntimeError("snapshot manifest has no files map")

    restored: list[str] = []
    skipped: list[str] = []
    for name, entry in files.items():
        if not isinstance(entry, dict) or not entry.get("source"):
            continue
        if name in CUSTOMER_DATA_KEYS and not restore_customer_data:
            skipped.append(name)
            continue
        dst = Path(str(entry["source"])).expanduser()
        if not _allowed_restore_path(paths, dst):
            raise RuntimeError(f"snapshot restore target is outside managed roots: {dst}")
        backup = snapshot / str(name)
        if entry.get("present"):
            if backup.exists():
                _restore_backup(backup, dst)
                restored.append(str(name))
        elif name in DELETE_IF_MISSING_KEYS:
            _remove_release_owned_path(dst)
            restored.append(str(name))

    report = {
        "snapshot": str(snapshot),
        "restored_at": utc_stamp(),
        "restore_customer_data": restore_customer_data,
        "restored": restored,
        "skipped": skipped,
    }
    atomic_write_json(snapshot / "rollback-report.json", report, mode=0o600)
    runner.log(f"[rollback] restored snapshot: {snapshot}")
    if skipped:
        runner.log("[rollback] preserved customer data keys: " + ", ".join(skipped))

    failures = verify_install(paths, public=verify_public) if verify_after else []
    return failures


def rollback(args: object) -> int:
    paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
    runner = Runner(verbose=True)
    snapshot = Path(args.snapshot).expanduser().resolve() if args.snapshot else latest_snapshot(paths)
    if not args.yes and sys.stdin.isatty():
        answer = input(f"Rollback using snapshot {snapshot}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Rollback cancelled.")
            return 2
    failures = restore_snapshot(
        paths,
        snapshot,
        runner,
        restore_customer_data=bool(args.restore_data),
        verify_public=bool(args.public),
        verify_after=not args.skip_verify,
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("Rollback completed.")
    return 0
