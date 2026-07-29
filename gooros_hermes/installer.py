from __future__ import annotations

import json
import os
import getpass
import shutil
import time
from pathlib import Path
from urllib.request import Request, urlopen

from .configstore import CustomerConfig, collect_customer_config, merge_env_values, validate_required, write_customer_files
from .constants import AGENTS, SPECIALISTS, VERSION
from .dashboard_patcher import build_live_dashboard
from .fsutil import atomic_write_json, atomic_write_text, copy_file, ensure_dir
from .paths import InstallPaths, asset_path, default_paths
from .proxy import caddy_hash_password, install_public_proxy, root_prefix, router_local_api_key, sslip_name, write_system_env
from .release import current_source_metadata
from .runner import Runner
from .safety import create_snapshot, write_install_state
from .verify import verify_install
from .yaml_merge import merge_telegram_group_config, remove_top_level_block


def preflight(runner: Runner, paths: InstallPaths, *, public_dashboards: bool, with_9router: bool) -> None:
    runner.log("[preflight] checking local requirements")
    if os.name != "posix" and not runner.dry_run:
        raise RuntimeError("full install target is Linux/macOS/WSL; run --dry-run on Windows for planning")
    for cmd in ("python3", "bash", "curl"):
        if not shutil.which(cmd) and not runner.dry_run:
            raise RuntimeError(f"required command missing: {cmd}")
    if public_dashboards and not runner.dry_run:
        root_prefix()
    if with_9router and not shutil.which("npm") and not runner.dry_run:
        runner.log("[preflight] npm missing; installer will try apt-get install nodejs npm")
    if runner.dry_run:
        runner.log(f"would ensure project dir: {paths.project_dir}")
        runner.log(f"would ensure config dir: {paths.config_dir}")
        runner.log(f"would ensure data dir: {paths.data_dir}")
        return
    ensure_dir(paths.project_dir)
    ensure_dir(paths.config_dir, 0o700)
    ensure_dir(paths.data_dir)


def install_hermes_if_needed(runner: Runner, *, with_hermes: bool) -> None:
    if shutil.which("hermes"):
        runner.log("[hermes] found existing Hermes CLI")
        return
    if not with_hermes:
        raise RuntimeError("Hermes CLI not found; rerun with --with-hermes")
    runner.log("[hermes] installing Hermes Agent using official install script")
    runner.shell("curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash", timeout=600)


def install_9router_if_requested(runner: Runner, *, requested: bool) -> None:
    if not requested:
        return
    if not shutil.which("npm"):
        if shutil.which("apt-get"):
            prefix = root_prefix()
            runner.run(prefix + ["apt-get", "update"], timeout=300)
            runner.run(prefix + ["apt-get", "install", "-y", "nodejs", "npm"], timeout=300)
        else:
            raise RuntimeError("npm is required to install 9Router")
    if not shutil.which("9router"):
        runner.run(["npm", "install", "-g", "9router"], timeout=600)


def _http_ok(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        request = Request(url, headers={"User-Agent": "gooros-hermes-installer"})
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 500, str(response.status)
    except Exception as exc:
        return False, str(exc)


def wait_for_9router(timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    detail = "not checked"
    while time.time() < deadline:
        ok, detail = _http_ok("http://127.0.0.1:20128/v1/models", timeout=3)
        if ok:
            return
        time.sleep(2)
    raise RuntimeError(f"9Router did not become reachable at http://127.0.0.1:20128/v1/models: {detail}")


def _provider_smoke_enabled() -> bool:
    return os.environ.get("GOOROS_9ROUTER_SMOKE", "1").strip().lower() not in {"0", "false", "no", "off"}


def smoke_9router_model(model: str, api_key: str = "", timeout: int = 90) -> None:
    if not _provider_smoke_enabled():
        return
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK only."}],
        "max_tokens": 8,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "gooros-hermes-installer",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        request = Request(
            "http://127.0.0.1:20128/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            body = response.read(256 * 1024).decode("utf-8", errors="replace")
        data = json.loads(body)
        if not data.get("choices"):
            raise RuntimeError(f"unexpected response: {body[:500]}")
    except Exception as exc:
        raise RuntimeError(
            "9Router model smoke test failed. Connect a working free provider/model in the 9Router dashboard "
            "or set GOOROS_9ROUTER_SMOKE=0 only for a non-provider update. "
            f"model={model}; detail={exc}"
        ) from exc


def install_shared_scripts(paths: InstallPaths, runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(f"would install shared scripts to {paths.hermes_home / 'agents' / '_shared'}")
        return
    shared = paths.hermes_home / "agents" / "_shared"
    ensure_dir(shared)
    for name in ("log-task-local.sh", "cleanup-logs.sh", "route_model.py", "route_and_run.sh"):
        copy_file(asset_path("shared", name), shared / name, mode=0o755)


def install_profiles(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    for agent in SPECIALISTS:
        profile_dir = paths.hermes_home / "profiles" / agent
        soul = profile_dir / "SOUL.md"
        if profile_dir.exists() and soul.exists() and "GOOROS-HERMES-MANAGED" not in soul.read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError(f"profile {agent} already exists and is not Gooros-managed; choose a merge strategy first")
        if not profile_dir.exists():
            runner.run(["hermes", "profile", "create", agent, "--clone", "--description", f"Gooros {agent} specialist"], timeout=120)
        if runner.dry_run:
            runner.log(f"would write managed SOUL.md and sanitize platforms for profile {agent}")
            runner.run(["hermes", "profile", "alias", agent, "--name", f"gooros-{agent}"], check=False, timeout=60)
            continue
        text = asset_path("profiles", agent, "SOUL.md.tmpl").read_text(encoding="utf-8").format(owner_name=config.owner_name or "the owner")
        atomic_write_text(soul, text)
        cfg = profile_dir / "config.yaml"
        if cfg.exists():
            remove_top_level_block(cfg, "platforms")
        runner.run(["hermes", "profile", "alias", agent, "--name", f"gooros-{agent}"], check=False, timeout=60)


def install_orchestrator_rules(paths: InstallPaths, config: CustomerConfig, runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(f"would write Orchestrator managed rules to {paths.hermes_home / 'GOOROS_ORCHESTRATOR.md'}")
        return
    text = f"""<!-- GOOROS-HERMES-MANAGED: orchestrator rules v1 -->
# Gooros Orchestrator Rules

Ten cua ban la Orchestrator. Chu so huu la {config.owner_name}.

Owner work: {config.owner_work}
Current focus: {config.owner_focus}
Timezone: {config.timezone}

Ban dieu phoi Scout, Scribe, Reach, va Dev. Ban khong chi chuyen tiep tin nhan; ban chiu trach nhiem ket qua cuoi cung, xac minh va bao cao lai ro rang.

Quy tac van hanh:
- Neu nhiem vu co nhieu hon mot buoc, gui mot dong trang thai ngan truoc moi buoc.
- Luon dua ke hoach truoc khi hanh dong voi cong viec co rui ro/nhieu buoc.
- Giao tiep ngan gon, ro rang; lua chon dung 1, 2, 3.
- Khi uy quyen, noi agent nao va vi sao trong mot dong.
- Khong bia dat ket qua; that bai thi noi thang.
- Truoc moi phan hoi, ghi log bang `bash ~/.hermes/agents/_shared/log-task-local.sh "orchestrator" "<brief description>" "<status>" "<model>"`.
- Tai lieu dai hon khoang 15 dong luu vao `~/agent-mission-control/content/orchestrator/`.
"""
    atomic_write_text(paths.hermes_home / "GOOROS_ORCHESTRATOR.md", text)


def install_logging(paths: InstallPaths, runner: Runner) -> None:
    install_shared_scripts(paths, runner)
    if runner.dry_run:
        runner.log("would smoke-test log-task-local.sh")
        return
    runner.run(["bash", str(paths.hermes_home / "agents" / "_shared" / "log-task-local.sh"), "dev", "installed Gooros logging system", "completed", "installer"], timeout=30)
    cron_line = f"0 3 * * 0 AGENT_LOG_DB={paths.project_dir / 'agent-logs.db'} bash {paths.hermes_home / 'agents' / '_shared' / 'cleanup-logs.sh'} >/tmp/gooros-cleanup-logs.log 2>&1"
    runner.log(f"[logging] weekly cleanup crontab line:\n{cron_line}")


def install_telegram_routing(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log("would merge Hermes Telegram env/config, install telegram_topic_profiles, enable multiplex_profiles, and restart gateway")
        return
    merge_env_values(
        paths.hermes_home / ".env",
        {
            "TELEGRAM_BOT_TOKEN": config.telegram_bot_token,
            "TELEGRAM_HOME_CHANNEL": config.telegram_home_channel or f"telegram:{config.telegram_chat_id}",
            "TELEGRAM_ALLOWED_USERS": config.telegram_allowed_users,
        },
    )
    config_path_result = runner.run(["hermes", "config", "path"], capture=True, check=False, timeout=30)
    config_path = Path(config_path_result.stdout.strip()).expanduser() if config_path_result.returncode == 0 and config_path_result.stdout.strip() else paths.hermes_home / "config.yaml"
    merge_telegram_group_config(config_path, config.telegram_chat_id)
    runner.run(["hermes", "config", "set", "--force", "multiplex_profiles", "true"], timeout=30)
    plugin_dir = paths.hermes_home / "plugins" / "telegram_topic_profiles"
    ensure_dir(plugin_dir)
    copy_file(asset_path("plugins", "telegram_topic_profiles", "plugin.yaml"), plugin_dir / "plugin.yaml")
    copy_file(asset_path("plugins", "telegram_topic_profiles", "__init__.py"), plugin_dir / "__init__.py")
    topics = {
        "chat_id": config.telegram_chat_id,
        "topics": {
            config.thread_scout: "scout",
            config.thread_scribe: "scribe",
            config.thread_reach: "reach",
            config.thread_dev: "dev",
        },
    }
    atomic_write_json(plugin_dir / "topics.json", topics, mode=0o600)
    runner.run(["hermes", "plugins", "enable", "telegram_topic_profiles"], check=False, timeout=60)
    runner.run(["hermes", "gateway", "restart"], check=False, timeout=120)


def install_dashboard(paths: InstallPaths, runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(f"would install dashboard files into {paths.project_dir}")
        return
    ensure_dir(paths.project_dir)
    copy_file(asset_path("dashboard", "server.py"), paths.project_dir / "server.py", mode=0o755)
    copy_file(asset_path("dashboard", "template.html"), paths.project_dir / "template.html")
    copy_file(asset_path("dashboard", "gooros-logo.png"), paths.project_dir / "gooros-logo.png")
    build_live_dashboard(paths.project_dir / "template.html", paths.project_dir / "index.html")
    ensure_dir(paths.project_dir / "content" / "orchestrator")
    for agent in SPECIALISTS:
        ensure_dir(paths.project_dir / "content" / agent)
    ensure_dir(paths.project_dir / "backups")


def write_model_routing(paths: InstallPaths, models: list[str], runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log("would write model-routing.json with deepseek-free-first policy")
        return
    if not models:
        raise RuntimeError("cannot write model routing without 9Router models")
    deepseek = next((m for m in models if "deepseek" in m.lower() and ("free" in m.lower() or ":free" in m.lower())), None)
    fast = deepseek or choose_9router_model(models)
    premium = next((m for m in models if m != fast), fast)
    data = {
        "policy": "deepseek-free-first",
        "models": [{"id": premium, "tier": "premium"}, {"id": fast, "tier": "fast"}],
        "complex_keywords": ["code", "debug", "architecture", "strategy", "multi-step", "longform", "reason"],
        "simple_keywords": ["short", "quick", "summary", "format", "caption", "rewrite"],
    }
    atomic_write_json(paths.hermes_home / "agents" / "_shared" / "model-routing.json", data, mode=0o600)


def choose_9router_model(models: list[str]) -> str:
    if not models:
        raise RuntimeError("9Router returned no models; connect a free provider in the 9Router dashboard, then rerun update")
    ranked = sorted(
        models,
        key=lambda m: (
            0
            if ("deepseek" in m.lower() and ("free" in m.lower() or m.lower().startswith(("kr/", "oc/", "opencode/"))))
            else 1
            if "deepseek" in m.lower()
            else 2
            if ("free" in m.lower() or m.lower().startswith(("kr/", "oc/", "opencode/")))
            else 3
            if "auto" in m.lower()
            else 4,
            m,
        ),
    )
    return ranked[0]


def configure_hermes_for_9router(runner: Runner, paths: InstallPaths, models: list[str]) -> str:
    selected = choose_9router_model(models)
    if runner.dry_run:
        runner.log(f"would configure Hermes provider custom -> 9Router with model {selected}")
        return selected
    api_key = router_local_api_key(paths) or os.environ.get("OPENAI_API_KEY") or "gooros-local-9router"
    merge_env_values(paths.hermes_home / ".env", {"OPENAI_API_KEY": api_key})
    runner.run(["hermes", "config", "set", "--force", "model.provider", "custom"], timeout=30)
    runner.run(["hermes", "config", "set", "--force", "model.base_url", "http://127.0.0.1:20128/v1"], timeout=30)
    runner.run(["hermes", "config", "set", "--force", "model.default", selected], timeout=30)
    return selected


def discover_9router_models() -> list[str]:
    try:
        with urlopen("http://127.0.0.1:20128/v1/models", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            return [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        return []


def _service_account() -> tuple[str, str, str, str]:
    if os.name != "posix":
        return "", "", "", ""
    import grp
    import pwd

    user = getpass.getuser()
    record = pwd.getpwnam(user)
    group = grp.getgrgid(record.pw_gid).gr_name
    path = os.environ.get("PATH", "")
    extras = ["/usr/local/bin", "/usr/bin", "/bin", str(Path(record.pw_dir) / ".local" / "bin")]
    for item in extras:
        if item and item not in path.split(":"):
            path = f"{path}:{item}" if path else item
    return user, group, record.pw_dir, path


def _required_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"required executable not found for systemd service: {name}")


def install_systemd_services(runner: Runner, paths: InstallPaths, *, with_9router: bool) -> None:
    if os.name != "posix":
        return
    if runner.dry_run:
        runner.log("would install systemd services for mission control, Hermes dashboard, and optionally 9Router")
        return
    prefix = root_prefix()
    services = ["gooros-mission-control.service", "hermes-native-dashboard.service"]
    if with_9router:
        services.append("9router.service")
    user, group, home, service_path = _service_account()
    replacements = {
        "%GOOROS_SERVICE_USER%": user,
        "%GOOROS_SERVICE_GROUP%": group,
        "%GOOROS_SERVICE_HOME%": home,
        "%GOOROS_SERVICE_PATH%": service_path,
        "%GOOROS_PROJECT_DIR%": str(paths.project_dir),
        "%GOOROS_PYTHON3%": _required_bin("python3"),
        "%GOOROS_HERMES_BIN%": _required_bin("hermes"),
        "%GOOROS_9ROUTER_BIN%": _required_bin("9router") if with_9router else "/usr/bin/false",
    }
    for service in services:
        src = asset_path("proxy", "systemd", service)
        text = src.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        if "%GOOROS_" in text:
            raise RuntimeError(f"unrendered systemd placeholder in {service}")
        tmp = Path("/tmp") / service
        atomic_write_text(tmp, text)
        runner.run(prefix + ["install", "-m", "0644", str(tmp), f"/etc/systemd/system/{service}"])
    runner.run(prefix + ["systemctl", "daemon-reload"], timeout=60)
    for service in services:
        runner.run(prefix + ["systemctl", "enable", "--now", service], timeout=120, check=False)
    for service in services:
        runner.run(prefix + ["systemctl", "restart", service], timeout=120, check=False)


def restart_gateway(runner: Runner) -> None:
    runner.run(["hermes", "gateway", "restart"], check=False, timeout=120)


def install(args) -> int:
    paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
    runner = Runner(dry_run=args.dry_run, verbose=True)
    config = collect_customer_config(args, interactive=not args.yes)
    missing = validate_required(config, public_dashboards=args.with_public_dashboards, require_telegram_token=True)
    if missing and not args.dry_run:
        raise RuntimeError("missing required config: " + ", ".join(missing))
    preflight(runner, paths, public_dashboards=args.with_public_dashboards, with_9router=args.with_9router)
    if not args.dry_run:
        snap = create_snapshot(paths, "pre-install")
        runner.log(f"[safety] snapshot: {snap}")
    install_hermes_if_needed(runner, with_hermes=args.with_hermes)
    install_9router_if_requested(runner, requested=args.with_9router)
    caddy_hash = caddy_hash_password(runner, config.dash_password) if args.with_public_dashboards else None
    if not args.dry_run:
        write_customer_files(paths, config, caddy_hash)
    write_system_env(runner, paths, config, caddy_hash or "") if (args.systemd or args.with_public_dashboards) else None
    install_orchestrator_rules(paths, config, runner)
    install_profiles(runner, paths, config)
    install_logging(paths, runner)
    install_telegram_routing(runner, paths, config)
    install_dashboard(paths, runner)
    if args.systemd:
        install_systemd_services(runner, paths, with_9router=args.with_9router)
    if args.with_9router:
        if not args.dry_run:
            wait_for_9router()
        models = ["dry-run/deepseek-free"] if args.dry_run else discover_9router_models()
        if not args.dry_run:
            smoke_9router_model(choose_9router_model(models), router_local_api_key(paths))
        selected_model = configure_hermes_for_9router(runner, paths, models)
        write_model_routing(paths, models or [selected_model], runner)
        if not args.dry_run:
            restart_gateway(runner)
    if args.with_public_dashboards:
        install_public_proxy(runner, paths, config, caddy_hash or "")
    if not args.dry_run:
        write_install_state(
            paths,
            {
                "project_dir": str(paths.project_dir),
                "hermes_home": str(paths.hermes_home),
                "public_dashboards": bool(args.with_public_dashboards),
                "with_9router": bool(args.with_9router),
                "systemd": bool(args.systemd),
                **current_source_metadata(),
            },
        )
    failures = [] if args.dry_run else verify_install(paths, public=args.with_public_dashboards, with_9router=args.with_9router)
    print_install_report(paths, config, caddy_hash, failures, public=args.with_public_dashboards, with_9router=args.with_9router, dry_run=args.dry_run)
    return 1 if failures else 0


def print_install_report(paths: InstallPaths, config: CustomerConfig, caddy_hash: str | None, failures: list[str], *, public: bool, with_9router: bool, dry_run: bool = False) -> None:
    print("\nGooros Hermes install report")
    print(f"Version: {VERSION}")
    print(f"Project: {paths.project_dir}")
    print(f"Hermes home: {paths.hermes_home}")
    print(f"Mission Control local: http://127.0.0.1:51763")
    if public:
        print(f"Mission Control HTTPS: https://{sslip_name('mission', config.public_ip)}")
        print(f"Hermes native HTTPS: https://{sslip_name('hermes', config.public_ip)}")
        print(f"9Router HTTPS: https://{sslip_name('router', config.public_ip)}")
        print(f"Auth user: {config.dash_user}")
        print(f"Auth password (shown once): {config.dash_password}")
    if with_9router:
        print("Hermes -> 9Router local endpoint: http://127.0.0.1:20128/v1")
        if public:
            print("9Router initial dashboard password: same as dashboard auth password on a fresh install")
        else:
            print("9Router initial dashboard password: stored in the local Gooros secrets file")
    if dry_run:
        print("\nVerification: skipped (dry-run/plan only)")
    elif failures:
        print("\nVerification failed:")
        for failure in failures:
            print(f"- {failure}")
    else:
        print("\nVerification: passed")
    print("\nTelegram reset step: send /new once in #scout, #scribe, #reach, #dev after first gateway restart if old sessions keep routing to Orchestrator.")
