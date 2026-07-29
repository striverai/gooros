from __future__ import annotations

import json
import os
import getpass
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

from .configstore import CustomerConfig, collect_customer_config, merge_env_values, validate_required, write_customer_files
from .constants import GOOROS_9ROUTER_API_KEY_NAME, GOOROS_9ROUTER_COMBO_NAME, SPECIALISTS, VERSION
from .dashboard_patcher import build_live_dashboard
from .fsutil import atomic_write_json, atomic_write_text, copy_file, ensure_dir
from .paths import InstallPaths, asset_path, default_paths
from .proxy import (
    caddy_hash_password,
    install_caddy_if_missing,
    install_public_proxy,
    root_prefix,
    router_initial_password,
    router_local_api_key,
    sslip_name,
    write_system_env,
)
from .release import current_source_metadata
from .runner import Runner
from .router_api import (
    REQUIRED_FREE_PROVIDERS,
    choose_router_model,
    discover_free_router_models,
    ensure_router_api_key,
    ensure_router_combo,
    ensure_router_round_robin,
)
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
    try:
        runner.shell("curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash", timeout=600, env={"CI": "1"})
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        if shutil.which("hermes"):
            runner.log(f"[hermes] install script exited before setup completed; Hermes CLI is present, continuing: {exc}")
            return
        raise RuntimeError(f"Hermes install script did not finish and Hermes CLI is still missing: {exc}") from exc
    if not shutil.which("hermes"):
        raise RuntimeError("Hermes install script completed, but Hermes CLI is still missing from PATH")


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


def hermes_plugin_enable_command(runner: Runner, plugin: str) -> list[str]:
    command = ["hermes", "plugins", "enable", plugin]
    result = runner.run(["hermes", "plugins", "enable", "--help"], capture=True, check=False, timeout=30)
    help_text = (result.stdout or "") + "\n" + (result.stderr or "")
    if "--no-allow-tool-override" in help_text:
        command.append("--no-allow-tool-override")
    return command


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
            "9Router combo smoke test failed. Connect a working free provider/model in the 9Router dashboard "
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


def install_telegram_routing(runner: Runner, paths: InstallPaths, config: CustomerConfig, *, systemd: bool = False) -> None:
    if runner.dry_run:
        runner.log("would merge Hermes Telegram env/config, install telegram_topic_profiles, enable multiplex_profiles, and install/start gateway")
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
    runner.run(hermes_plugin_enable_command(runner, "telegram_topic_profiles"), check=False, timeout=60, input_text="n\n")
    restart_gateway(runner, systemd=systemd)


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


def write_model_routing(paths: InstallPaths, combo_name: str, models: list[str], runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(f"would write model-routing.json for 9Router combo {combo_name}")
        return
    if not models:
        raise RuntimeError("cannot write model routing without free 9Router models")
    required_providers = [
        {
            "id": spec.provider_id,
            "name": spec.display_name,
            "alias": spec.alias,
            "members": [
                model for model in models
                if model.startswith(f"{spec.alias}/") or model.startswith(f"{spec.provider_id}/")
            ],
        }
        for spec in REQUIRED_FREE_PROVIDERS
    ]
    data = {
        "policy": "9router-free-combo-round-robin",
        "combo": {
            "name": combo_name,
            "strategy": "round-robin",
            "priority": "deepseek-first",
            "members": models,
            "preferred_model": choose_9router_model(models),
            "required_providers": required_providers,
        },
        "models": [{"id": combo_name, "tier": "fast"}],
        "complex_keywords": ["code", "debug", "architecture", "strategy", "multi-step", "longform", "reason"],
        "simple_keywords": ["short", "quick", "summary", "format", "caption", "rewrite"],
    }
    atomic_write_json(paths.hermes_home / "agents" / "_shared" / "model-routing.json", data, mode=0o600)


def choose_9router_model(models: list[str]) -> str:
    return choose_router_model(models)


def configure_hermes_for_9router(runner: Runner, paths: InstallPaths, combo_name: str, api_key: str) -> str:
    if runner.dry_run:
        runner.log(f"would configure Hermes root + profiles -> 9Router combo {combo_name}")
        return combo_name
    if not api_key:
        raise RuntimeError("cannot configure Hermes for 9Router without a real 9Router API key")
    targets: list[tuple[str, Path, list[str]]] = [
        ("orchestrator", paths.hermes_home, ["hermes"]),
    ]
    for agent in SPECIALISTS:
        profile_dir = paths.hermes_home / "profiles" / agent
        if profile_dir.exists():
            targets.append((agent, profile_dir, ["hermes", "-p", agent]))
    for agent, root, command in targets:
        merge_env_values(root / ".env", {"OPENAI_API_KEY": api_key})
        runner.run(command + ["config", "set", "--force", "model.provider", "custom"], timeout=30)
        runner.run(command + ["config", "set", "--force", "model.base_url", "http://127.0.0.1:20128/v1"], timeout=30)
        runner.run(command + ["config", "set", "--force", "model.default", combo_name], timeout=30)
        runner.log(f"[9router] Hermes profile {agent} default model -> {combo_name}")
    return combo_name


def discover_9router_models() -> list[str]:
    discovery = discover_free_router_models()
    return discovery.models


def ensure_9router_hosted_combo(runner: Runner, paths: InstallPaths) -> tuple[str, list[str], str]:
    if runner.dry_run:
        models = ["dry-run/deepseek-free", "dry-run/qwen-free"]
        runner.log(
            f"would create/reconcile 9Router API key '{GOOROS_9ROUTER_API_KEY_NAME}', combo "
            f"{GOOROS_9ROUTER_COMBO_NAME}, and round-robin settings"
        )
        return GOOROS_9ROUTER_COMBO_NAME, models, "DRY_RUN_9ROUTER_API_KEY"
    key_info = ensure_router_api_key(GOOROS_9ROUTER_API_KEY_NAME, preferred_key=router_local_api_key(paths))
    api_key = str(key_info.get("key") or "").strip()
    if not api_key:
        raise RuntimeError("9Router did not return a real API key for Hermes")
    merge_env_values(paths.secrets_env, {"GOOROS_9ROUTER_API_KEY": api_key})
    discovery = discover_free_router_models()
    for warning in discovery.warnings:
        runner.log(f"[9router] model discovery warning: {warning}")
    if discovery.missing_required_providers:
        missing = ", ".join(discovery.missing_required_providers)
        required = ", ".join(f"{spec.display_name} ({spec.alias}/...)" for spec in REQUIRED_FREE_PROVIDERS)
        raise RuntimeError(
            "9Router free combo is incomplete. Gooros requires every free model exposed by "
            f"{required}; missing provider model list: {missing}. Check 9Router dashboard auth/network, "
            "then rerun gooros-hermes update."
        )
    for spec in REQUIRED_FREE_PROVIDERS:
        count = len(discovery.required_provider_models.get(spec.provider_id, []))
        runner.log(f"[9router] {spec.display_name} models in combo: {count}")
    if not discovery.models:
        raise RuntimeError(
            "9Router has no free models available for the Gooros combo. Connect at least one working free/free-tier "
            "provider in the 9Router dashboard, then rerun gooros-hermes update."
        )
    ensure_router_combo(GOOROS_9ROUTER_COMBO_NAME, discovery.models, kind="llm")
    ensure_router_round_robin(sticky_limit=1)
    return GOOROS_9ROUTER_COMBO_NAME, discovery.models, api_key


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


def _candidate_9router_server_paths(router_bin: str) -> list[Path]:
    executable = Path(router_bin).expanduser().resolve()
    candidates: list[Path] = []
    for parent in (executable.parent, *executable.parents):
        candidates.append(parent / "app" / "server.js")
        candidates.append(parent / "node_modules" / "9router" / "app" / "server.js")
    return candidates


def resolve_9router_server_js(runner: Runner, router_bin: str) -> Path:
    candidates = _candidate_9router_server_paths(router_bin)
    if shutil.which("npm"):
        result = runner.run(["npm", "root", "-g"], capture=True, check=False, timeout=30)
        npm_root = (result.stdout or "").strip()
        if result.returncode == 0 and npm_root:
            candidates.append(Path(npm_root).expanduser() / "9router" / "app" / "server.js")
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    searched = "\n".join(str(path) for path in candidates[:8])
    raise RuntimeError(
        "9Router server entrypoint app/server.js was not found after npm install. "
        "The 9Router package layout may have changed; inspect the installed package before enabling systemd.\n"
        f"Searched:\n{searched}"
    )


def seed_router_management_env(paths: InstallPaths) -> None:
    password = router_initial_password(paths)
    if not password:
        return
    os.environ.setdefault("GOOROS_9ROUTER_INITIAL_PASSWORD", password)
    os.environ.setdefault("INITIAL_PASSWORD", password)


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
    router_bin = _required_bin("9router") if with_9router else "/usr/bin/false"
    router_server = resolve_9router_server_js(runner, router_bin) if with_9router else Path("/usr/bin/false")
    replacements = {
        "%GOOROS_SERVICE_USER%": user,
        "%GOOROS_SERVICE_GROUP%": group,
        "%GOOROS_SERVICE_HOME%": home,
        "%GOOROS_SERVICE_PATH%": service_path,
        "%GOOROS_PROJECT_DIR%": str(paths.project_dir),
        "%GOOROS_PYTHON3%": _required_bin("python3"),
        "%GOOROS_HERMES_BIN%": _required_bin("hermes"),
        "%GOOROS_9ROUTER_BIN%": router_bin,
        "%GOOROS_NODE_BIN%": _required_bin("node") if with_9router else "/usr/bin/false",
        "%GOOROS_9ROUTER_APP_DIR%": str(router_server.parent),
        "%GOOROS_9ROUTER_SERVER_JS%": str(router_server),
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


def _gateway_system_args(systemd: bool) -> list[str]:
    return ["--system"] if systemd and os.name == "posix" else []


def _gateway_env() -> dict[str, str]:
    return {"HERMES_ACCEPT_HOOKS": "1"}


def restart_gateway(runner: Runner, *, systemd: bool = False) -> None:
    if runner.dry_run:
        runner.log("would ensure Hermes gateway service is installed, started, and restarted")
        return
    system_args = _gateway_system_args(systemd)
    base = ["hermes", "gateway", "--accept-hooks"]
    restart = runner.run(base + ["restart", *system_args], check=False, capture=True, timeout=120, env=_gateway_env())
    if restart.returncode != 0:
        detail = (restart.stderr or restart.stdout or "").strip()
        if detail:
            runner.log(f"[telegram] gateway restart did not find a ready service: {detail}")
        install_cmd = base + ["install", "--start-now", "--start-on-login", *system_args]
        if system_args:
            user, _, _, _ = _service_account()
            install_cmd.extend(["--run-as-user", user])
        runner.run(install_cmd, check=False, capture=True, timeout=180, env=_gateway_env())
    runner.run(base + ["start", "--all", *system_args], check=False, capture=True, timeout=120, env=_gateway_env())
    status = runner.run(base + ["status", "--deep", *system_args], check=False, capture=True, timeout=60, env=_gateway_env())
    if status.returncode != 0:
        detail = (status.stderr or status.stdout or "").strip()
        runner.log(f"[telegram] gateway status warning: {detail or 'status command failed'}")


def restart_systemd_services(runner: Runner, *, with_9router: bool) -> None:
    if os.name != "posix":
        return
    if runner.dry_run:
        runner.log("would restart managed systemd services after 9Router auth/model changes")
        return
    services = ["gooros-mission-control.service", "hermes-native-dashboard.service"]
    if with_9router:
        services.insert(0, "9router.service")
    prefix = root_prefix()
    for service in services:
        runner.run(prefix + ["systemctl", "restart", service], timeout=120, check=False)


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
    if args.with_public_dashboards:
        install_caddy_if_missing(runner)
    caddy_hash = caddy_hash_password(runner, config.dash_password) if args.with_public_dashboards else None
    if not args.dry_run:
        write_customer_files(paths, config, caddy_hash)
    write_system_env(runner, paths, config, caddy_hash or "") if (args.systemd or args.with_public_dashboards) else None
    if args.with_9router and not args.dry_run:
        seed_router_management_env(paths)
    install_orchestrator_rules(paths, config, runner)
    install_profiles(runner, paths, config)
    install_logging(paths, runner)
    install_telegram_routing(runner, paths, config, systemd=args.systemd)
    install_dashboard(paths, runner)
    if args.systemd:
        install_systemd_services(runner, paths, with_9router=args.with_9router)
    if args.with_9router:
        if not args.dry_run:
            wait_for_9router()
        combo_name, models, api_key = ensure_9router_hosted_combo(runner, paths)
        write_model_routing(paths, combo_name, models, runner)
        if not args.dry_run:
            if args.systemd or args.with_public_dashboards:
                write_system_env(runner, paths, config, caddy_hash or "")
            smoke_9router_model(combo_name, api_key)
        configure_hermes_for_9router(runner, paths, combo_name, api_key)
        if not args.dry_run:
            restart_gateway(runner, systemd=args.systemd)
            if args.systemd:
                restart_systemd_services(runner, with_9router=True)
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
    failures = [] if args.dry_run else verify_install(
        paths,
        public=args.with_public_dashboards,
        with_9router=args.with_9router,
        auth_user=config.dash_user,
        auth_password=config.dash_password if args.with_public_dashboards else "",
    )
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
        print(f"Hermes default model: {GOOROS_9ROUTER_COMBO_NAME} (9Router free combo, DeepSeek-first, round-robin)")
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
