from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from .configstore import read_caddy_hash, read_customer_files, validate_required
from .constants import PRODUCT, VERSION
from .fsutil import atomic_write_json, ensure_dir, read_json, sha256_file, utc_stamp
from .installer import (
    configure_hermes_for_9router,
    ensure_9router_hosted_combo,
    install_9router_if_requested,
    install_dashboard,
    install_logging,
    install_orchestrator_rules,
    install_profiles,
    install_public_proxy,
    install_systemd_services,
    install_telegram_routing,
    preflight,
    restart_gateway,
    restart_systemd_services,
    seed_router_management_env,
    smoke_9router_model,
    wait_for_9router,
    write_model_routing,
    write_system_env,
)
from .paths import InstallPaths, default_paths
from .release import (
    SKIP_DIRS,
    compare_versions,
    current_source_metadata,
    migration_ids,
    read_release_manifest,
    release_version,
    sanitize_release_id,
    scan_for_secret_paths,
    tree_checksum,
    validate_release_manifest,
    version_tuple,
)
from .rollback import restore_snapshot
from .runner import Runner
from .safety import create_snapshot, write_install_state
from .verify import verify_install


def _run_git(runner: Runner, repo: Path, *args: str, check: bool = True) -> str:
    result = runner.run(["git", "-C", str(repo), *args], capture=True, check=check, timeout=300)
    return (result.stdout or "").strip()


def _latest_git_ref(runner: Runner, repo: Path) -> str:
    tags = [line.strip() for line in _run_git(runner, repo, "tag", "--list").splitlines() if line.strip()]
    semver_tags = [tag for tag in tags if re.match(r"^v?\d+(\.\d+){0,2}([+-].*)?$", tag)]
    if semver_tags:
        return sorted(semver_tags, key=version_tuple)[-1]
    head = _run_git(runner, repo, "symbolic-ref", "refs/remotes/origin/HEAD", check=False)
    if head.startswith("refs/remotes/"):
        return head.removeprefix("refs/remotes/")
    return "origin/main"


def _resolve_repo_url(args: object, installed: dict[str, object]) -> str:
    arg_url = getattr(args, "repo_url", None)
    if arg_url:
        return str(arg_url)
    if installed.get("release_repo"):
        return str(installed["release_repo"])
    if os.environ.get("GOOROS_HERMES_REPO"):
        return str(os.environ["GOOROS_HERMES_REPO"])
    meta = current_source_metadata()
    return meta.get("release_repo", "")


def _ensure_repo_cache(runner: Runner, paths: InstallPaths, repo_url: str) -> Path:
    if not shutil.which("git"):
        raise RuntimeError("git is required for gooros-hermes update")
    repo = paths.data_dir / "repo"
    if (repo / ".git").exists():
        _run_git(runner, repo, "fetch", "--tags", "--prune")
        return repo
    if repo.exists() and any(repo.iterdir()):
        raise RuntimeError(f"repo cache exists but is not a git checkout: {repo}")
    if not repo_url or "<org>" in repo_url:
        raise RuntimeError("release repo URL is not configured; rerun install from a real repo or pass --repo-url")
    ensure_dir(repo.parent)
    runner.run(["git", "clone", repo_url, str(repo)], timeout=900)
    return repo


def _prepare_source(args: object, paths: InstallPaths, installed: dict[str, object], runner: Runner) -> tuple[Path, str, str, Path | None, str]:
    source_dir = getattr(args, "source_dir", None)
    if source_dir:
        source = Path(source_dir).expanduser().resolve()
        manifest = read_release_manifest(source)
        validate_release_manifest(source, manifest)
        meta = current_source_metadata(source)
        checksum = tree_checksum(source, [str(p) for p in manifest.get("release_owned", [])])
        revision = meta.get("release_revision") or checksum[:24]
        return source, revision, str(getattr(args, "target", None) or "local-source"), source if (source / ".git").exists() else None, meta.get("release_repo", "")

    repo_url = _resolve_repo_url(args, installed)
    repo = _ensure_repo_cache(runner, paths, repo_url)
    target = str(getattr(args, "target", None) or os.environ.get("GOOROS_HERMES_REF") or "latest")
    ref = _latest_git_ref(runner, repo) if target == "latest" else target
    _run_git(runner, repo, "checkout", "--detach", ref)
    revision = _run_git(runner, repo, "rev-parse", "HEAD")
    return repo, revision, ref, repo, repo_url


def _copy_source_to_stage(source: Path, stage: Path) -> None:
    release_root = stage.parent.resolve()
    stage_resolved = stage.resolve()
    if not stage_resolved.is_relative_to(release_root):
        raise RuntimeError(f"refusing to stage outside release dir: {stage}")
    if stage.exists():
        shutil.rmtree(stage)

    def ignore(_: str, names: list[str]) -> set[str]:
        ignored = set()
        for name in names:
            if name in SKIP_DIRS or name.endswith(".pyc"):
                ignored.add(name)
        return ignored

    ensure_dir(stage.parent)
    shutil.copytree(source, stage, ignore=ignore, symlinks=True)


def _resolve_flags(args: object, installed: dict[str, object]) -> tuple[bool, bool, bool]:
    public = bool(installed.get("public_dashboards", False))
    with_9router = bool(installed.get("with_9router", False))
    systemd = bool(installed.get("systemd", False))
    if getattr(args, "public", False):
        public = True
    if getattr(args, "no_public", False):
        public = False
    if getattr(args, "with_9router", False):
        with_9router = True
    if getattr(args, "no_9router", False):
        with_9router = False
    if getattr(args, "systemd", False):
        systemd = True
    if getattr(args, "no_systemd", False):
        systemd = False
    return public, with_9router, systemd


def _print_update_plan(
    paths: InstallPaths,
    installed: dict[str, object],
    manifest: dict[str, object],
    *,
    source: Path,
    revision: str,
    ref: str,
    release_id: str,
    public: bool,
    with_9router: bool,
    systemd: bool,
) -> None:
    release_owned = manifest.get("release_owned", [])
    migrations = migration_ids(manifest)
    print("\nGooros Hermes update plan")
    print(f"From version: {installed.get('version', 'unknown')}")
    print(f"To version: {release_version(manifest)}")
    print(f"Target ref: {ref}")
    print(f"Revision: {revision[:12] if revision else 'unknown'}")
    print(f"Source: {source}")
    print(f"Release id: {release_id}")
    print(f"Project: {paths.project_dir}")
    print(f"Hermes home: {paths.hermes_home}")
    print("\nActions:")
    print("- fetch/prepare source release")
    print("- validate manifest and scan for secret-looking files")
    print("- snapshot customer instance before mutation")
    print("- stage release under data_dir/releases")
    print("- run idempotent migrations")
    print("- replace release-owned dashboard/plugin/profile/shared files")
    print("- merge-managed Hermes/Telegram/Caddy settings only")
    if systemd:
        print("- restart managed systemd services")
    print("- run post-update verification")
    print("- switch current release only after success")
    print("\nCustomer-owned data preserved:")
    print("- board.db, agent-logs.db, content/, Hermes state.db, kanban.db, profile state, sessions, secrets")
    print(f"\nModules: public_dashboards={public}, 9router={with_9router}, systemd={systemd}")
    print(f"Release-owned paths: {len(release_owned)}")
    print(f"Migrations: {', '.join(migrations) if migrations else 'none'}")


def _migration_db(paths: InstallPaths) -> Path:
    return paths.project_dir / ".gooros" / "migrations.db"


def _load_migration_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"gooros_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_migrations(staged: Path, paths: InstallPaths, manifest: dict[str, object], runner: Runner) -> None:
    ensure_dir(_migration_db(paths).parent)
    with sqlite3.connect(_migration_db(paths)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migrations (
              id TEXT PRIMARY KEY,
              checksum TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              completed_at TEXT,
              error TEXT
            )
            """
        )
        for migration_id in migration_ids(manifest):
            migration_path = staged / "migrations" / f"{migration_id}.py"
            if not migration_path.exists():
                raise RuntimeError(f"migration listed in manifest but missing: {migration_id}")
            checksum = sha256_file(migration_path)
            row = conn.execute("SELECT checksum, status FROM migrations WHERE id = ?", (migration_id,)).fetchone()
            if row and row[0] == checksum and row[1] == "completed":
                runner.log(f"[migration] already applied: {migration_id}")
                continue
            if row and row[0] != checksum and row[1] == "completed":
                raise RuntimeError(f"migration checksum changed after completion: {migration_id}")
            started = utc_stamp()
            conn.execute(
                """
                INSERT INTO migrations(id, checksum, status, started_at, error)
                VALUES(?, ?, 'running', ?, NULL)
                ON CONFLICT(id) DO UPDATE SET checksum=excluded.checksum, status='running', started_at=excluded.started_at, error=NULL
                """,
                (migration_id, checksum, started),
            )
            conn.commit()
            try:
                module = _load_migration_module(migration_path)
                apply = getattr(module, "apply", None)
                if callable(apply):
                    apply(paths, runner)
                conn.execute(
                    "UPDATE migrations SET status='completed', completed_at=?, error=NULL WHERE id=?",
                    (utc_stamp(), migration_id),
                )
                conn.commit()
                runner.log(f"[migration] completed: {migration_id}")
            except Exception as exc:
                conn.execute(
                    "UPDATE migrations SET status='failed', error=? WHERE id=?",
                    (str(exc), migration_id),
                )
                conn.commit()
                raise


def _switch_current_release(paths: InstallPaths, final_release: Path) -> None:
    ensure_dir(paths.data_dir)
    current = paths.current_release
    tmp = paths.data_dir / ".current.tmp"
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_dir() and not tmp.is_symlink():
            shutil.rmtree(tmp)
        else:
            tmp.unlink()
    try:
        tmp.symlink_to(final_release, target_is_directory=True)
        os.replace(tmp, current)
    except OSError:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        atomic_write_json(paths.data_dir / "current.json", {"path": str(final_release)}, mode=0o600)


def _finalize_release(staged: Path, final_release: Path, release_root: Path, force: bool) -> None:
    release_root = release_root.resolve()
    if not final_release.resolve().is_relative_to(release_root):
        raise RuntimeError(f"refusing to finalize outside release dir: {final_release}")
    if final_release.exists():
        if not force:
            raise RuntimeError(f"release already staged/finalized: {final_release}; rerun with --force to repair")
        shutil.rmtree(final_release)
    staged.rename(final_release)


def cmd_apply_staged(args: object) -> int:
    paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
    runner = Runner(verbose=True)
    staged = Path(args.staged_dir).expanduser().resolve()
    manifest = read_release_manifest(staged)
    validate_release_manifest(staged, manifest)
    findings = scan_for_secret_paths(staged)
    if findings:
        raise RuntimeError("secret-looking files found in release source:\n- " + "\n- ".join(findings))

    installed = read_json(paths.install_state, default={})
    installed = installed if isinstance(installed, dict) else {}
    public, with_9router, systemd = _resolve_flags(args, installed)
    config = read_customer_files(paths)
    missing = validate_required(config, public_dashboards=public)
    if missing:
        raise RuntimeError("stored customer config is missing required fields: " + ", ".join(missing))
    pass_hash = read_caddy_hash(paths) if public else ""
    if public and not pass_hash:
        raise RuntimeError("dashboard auth hash is missing; rotate auth before public dashboard update")
    if not shutil.which("hermes"):
        raise RuntimeError("Hermes CLI not found; update cannot preserve/reconfigure profiles safely")

    preflight(runner, paths, public_dashboards=public, with_9router=with_9router)
    apply_migrations(staged, paths, manifest, runner)
    install_9router_if_requested(runner, requested=with_9router)
    install_orchestrator_rules(paths, config, runner)
    install_profiles(runner, paths, config)
    install_logging(paths, runner)
    install_telegram_routing(runner, paths, config, systemd=systemd)
    install_dashboard(paths, runner)
    if systemd or public:
        write_system_env(runner, paths, config, pass_hash)
    if with_9router:
        seed_router_management_env(paths)
    if systemd:
        install_systemd_services(runner, paths, with_9router=with_9router)
    if with_9router:
        wait_for_9router()
        combo_name, models, api_key = ensure_9router_hosted_combo(runner, paths)
        write_model_routing(paths, combo_name, models, runner)
        if systemd or public:
            write_system_env(runner, paths, config, pass_hash)
        smoke_9router_model(combo_name, api_key)
        configure_hermes_for_9router(runner, paths, combo_name, api_key)
        restart_gateway(runner, systemd=systemd)
        if systemd:
            restart_systemd_services(runner, with_9router=True)
    if public:
        install_public_proxy(runner, paths, config, pass_hash)

    failures = [] if args.skip_verify else verify_install(paths, public=public, with_9router=with_9router)
    if failures:
        raise RuntimeError("post-update verification failed:\n- " + "\n- ".join(failures))

    target_version = release_version(manifest)
    final_release = paths.release_dir / args.release_id
    _finalize_release(staged, final_release, paths.release_dir, force=bool(args.force))
    _switch_current_release(paths, final_release)
    write_install_state(
        paths,
        {
            "version": target_version,
            "schema_version": manifest.get("schema_version", 1),
            "updated_at": utc_stamp(),
            "release_id": args.release_id,
            "release_revision": args.source_revision,
            "release_ref": args.target_ref,
            "release_repo": args.repo_url or "",
            "release_checksum": tree_checksum(final_release, [str(p) for p in manifest.get("release_owned", [])]),
            "snapshot": args.snapshot,
            "public_dashboards": public,
            "with_9router": with_9router,
            "systemd": systemd,
        },
    )
    print(f"Update applied: {target_version} ({args.release_id})")
    return 0


def update(args: object) -> int:
    paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
    runner = Runner(verbose=True)
    installed = read_json(paths.install_state, default={})
    if not isinstance(installed, dict) or installed.get("product") != PRODUCT:
        raise RuntimeError(f"Gooros Hermes install state not found at {paths.install_state}; run install first")

    source, revision, ref, repo_for_pip, repo_url = _prepare_source(args, paths, installed, runner)
    manifest = read_release_manifest(source)
    validate_release_manifest(source, manifest)
    findings = scan_for_secret_paths(source)
    if findings:
        raise RuntimeError("secret-looking files found in release source:\n- " + "\n- ".join(findings))

    target_version = release_version(manifest)
    current_version = str(installed.get("version", "0.0.0"))
    if compare_versions(target_version, current_version) < 0 and not args.allow_downgrade:
        raise RuntimeError(f"target version {target_version} is older than installed {current_version}; use --allow-downgrade")
    if (
        not args.force
        and target_version == current_version
        and revision
        and installed.get("release_revision") == revision
    ):
        print(f"Gooros Hermes is already up to date: {target_version} ({revision[:12]})")
        return 0

    release_id = sanitize_release_id(target_version, revision)
    public, with_9router, systemd = _resolve_flags(args, installed)
    _print_update_plan(
        paths,
        installed,
        manifest,
        source=source,
        revision=revision,
        ref=ref,
        release_id=release_id,
        public=public,
        with_9router=with_9router,
        systemd=systemd,
    )
    if args.plan or args.dry_run:
        print("\nPlan only. Runtime was not changed.")
        return 0

    snapshot = create_snapshot(paths, f"pre-update-{current_version}-to-{target_version}")
    runner.log(f"[safety] snapshot: {snapshot}")
    stage = paths.release_dir / f"{release_id}.staging"
    _copy_source_to_stage(source, stage)
    transaction = {
        "product": PRODUCT,
        "started_at": utc_stamp(),
        "from_version": current_version,
        "to_version": target_version,
        "target_ref": ref,
        "source_revision": revision,
        "source": str(source),
        "staged": str(stage),
        "snapshot": str(snapshot),
        "release_id": release_id,
    }
    ensure_dir(paths.data_dir / "updates")
    atomic_write_json(paths.data_dir / "updates" / f"{utc_stamp()}-{release_id}.json", transaction, mode=0o600)

    apply_cmd = [
        sys.executable,
        "-m",
        "gooros_hermes.cli",
        "_apply-staged",
        "--staged-dir",
        str(stage),
        "--snapshot",
        str(snapshot),
        "--release-id",
        release_id,
        "--source-revision",
        revision,
        "--target-ref",
        ref,
        "--repo-url",
        repo_url,
    ]
    if args.skip_verify:
        apply_cmd.append("--skip-verify")
    if args.force:
        apply_cmd.append("--force")
    if public:
        apply_cmd.append("--public")
    else:
        apply_cmd.append("--no-public")
    if with_9router:
        apply_cmd.append("--with-9router")
    else:
        apply_cmd.append("--no-9router")
    if systemd:
        apply_cmd.append("--systemd")
    else:
        apply_cmd.append("--no-systemd")
    for key, value in (
        ("--hermes-home", args.hermes_home),
        ("--project-dir", args.project_dir),
        ("--config-dir", args.config_dir),
        ("--data-dir", args.data_dir),
    ):
        if value:
            apply_cmd.extend([key, value])

    env = {"PYTHONPATH": str(stage) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    result = runner.run(apply_cmd, check=False, timeout=1800, env=env)
    if result.returncode != 0:
        runner.log("[update] apply failed; restoring pre-update snapshot")
        failures = restore_snapshot(paths, snapshot, runner, restore_customer_data=True, verify_public=public, verify_after=False)
        if failures:
            for failure in failures:
                print(f"ROLLBACK VERIFY FAIL: {failure}")
        return result.returncode or 1

    if repo_for_pip and not args.no_reinstall_cli:
        runner.run([sys.executable, "-m", "pip", "install", "--user", "-e", str(repo_for_pip)], check=False, timeout=600)
    print(f"Gooros Hermes updated to {target_version}.")
    return 0
