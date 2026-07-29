from __future__ import annotations

import compileall
from email.message import Message
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gooros_hermes.dashboard_patcher import build_live_dashboard
from gooros_hermes.configstore import CustomerConfig
from gooros_hermes.installer import choose_9router_model, hermes_plugin_enable_command, resolve_9router_server_js, restart_gateway, write_model_routing
from gooros_hermes.paths import InstallPaths
from gooros_hermes.proxy import _caddy_version_tuple, render_caddy_block
from gooros_hermes.release import read_release_manifest, validate_release_manifest
from gooros_hermes.router_api import (
    GOOROS_9ROUTER_COMBO_NAME,
    REQUIRED_FREE_PROVIDERS,
    _auth_cookie_from_headers,
    rank_router_models,
    select_free_router_models,
)
from gooros_hermes import __version__
from gooros_hermes.constants import VERSION


GOOROS_LOGO_URL = (
    "https://lh3.googleusercontent.com/pw/"
    "AP1GczPr8NBMmnzZc_CES2G0Sa-AqmGF_qN2hpNKAPB0OzeUorrSa-YMthSEJ8L5_sfrKEKDME57Wz_ou7jtSBdNuDi0xY_88AOEDS2eTimErPxaGRTpcP7oPN6eXjKnVWGQdDmte8XgAYx4ksTXOe7XPIc=w1378-h234-s-no-gm"
)


def main() -> int:
    ok = compileall.compile_dir(ROOT / "gooros_hermes", quiet=1)
    ok = compileall.compile_dir(ROOT / "migrations", quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "dashboard" / "server.py"), quiet=1) and ok
    manifest = read_release_manifest(ROOT)
    validate_release_manifest(ROOT, manifest)
    assert __version__ == VERSION
    assert manifest["version"] == VERSION
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == VERSION
    for migration_id in manifest["migrations"]:
        assert (ROOT / "migrations" / f"{migration_id}.py").exists()
    assert (ROOT / "assets" / "dashboard" / "gooros-logo.png").exists()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "index.html"
        build_live_dashboard(ROOT / "assets" / "dashboard" / "template.html", out)
        text = out.read_text(encoding="utf-8")
        assert '<html lang="vi">' in text
        assert "const I18N_DEFAULT_LANG = 'vi';" in text
        assert 'id="lang-toggle"' in text
        assert 'id="lang-toggle-m"' in text
        assert "Gooros — Tổng quan Mission Control" in text
        assert f'<img src="{GOOROS_LOGO_URL}"' in text
        assert '<img src="/gooros-logo.png"' not in text
        old_dashboard_copy = (
            "Hermes is coordinating",
            "Hermes Core",
            "Hermes Jobs",
            "HERMES JOBS",
            "Hermes Mission Control",
            "Hermes agent",
            "Hermes data",
            "Hermes home",
            "Hermes cron",
            "real Hermes",
            "Hermes HQ",
            "Hermes · HQ",
            "hermes cron create",
        )
        for old_copy in old_dashboard_copy:
            assert old_copy not in text
        assert "DEMO_" not in text
        assert "hard-coded reply" not in text
        old_demo_data = (
            "Pulled 14 sources",
            "Routing directive #412",
            "Sweeping 14 sources",
            "node 0x9f",
            "claude-sonnet-4.5",
            "gemini-2.5-pro",
            "text-embed-3-large",
            "Outline next week's video script",
        )
        for old in old_demo_data:
            assert old not in text
        assert "hydrate(); connectSSE(); startPolling();" in text
    assert choose_9router_model(["openai/gpt-4o", "kr/deepseek-3.2", "oc/qwen3-coder"]) == "kr/deepseek-3.2"
    assert choose_9router_model(["paid/model", "free/model"]) == "free/model"
    assert rank_router_models(["oc/qwen-free", "kr/deepseek-3.2", "kr/deepseek-3.2"]) == ["kr/deepseek-3.2", "oc/qwen-free"]
    assert GOOROS_9ROUTER_COMBO_NAME == "gooros-free-combo"
    assert [spec.provider_id for spec in REQUIRED_FREE_PROVIDERS] == ["opencode", "mimo-free"]
    selected = select_free_router_models(
        providers=[{"id": "mimo-conn", "provider": "mimo-free", "name": "MiMo Code Free"}],
        provider_models_by_connection_id={"mimo-conn": [{"id": "mimo-extra"}]},
        pricing={},
        suggested_models_by_provider_id={
            "opencode": [{"id": "deepseek-v4-flash-free"}, {"id": "big-pickle"}],
            "mimo-free": [{"id": "mimo-auto"}],
        },
        fallback_models=[{"id": GOOROS_9ROUTER_COMBO_NAME, "owned_by": "combo"}, {"id": "oc/qwen-free"}],
    )
    assert selected.missing_required_providers == []
    assert selected.models[0] == "oc/deepseek-v4-flash-free"
    for required_model in ("oc/big-pickle", "oc/qwen-free", "mmf/mimo-auto", "mmf/mimo-extra"):
        assert required_model in selected.models
    assert all("/" in model for model in selected.models)
    with tempfile.TemporaryDirectory() as tmp_routing:
        paths = InstallPaths(
            hermes_home=Path(tmp_routing) / ".hermes",
            project_dir=Path(tmp_routing) / "project",
            config_dir=Path(tmp_routing) / "config",
            data_dir=Path(tmp_routing) / "data",
        )
        write_model_routing(paths, GOOROS_9ROUTER_COMBO_NAME, selected.models)
        routing = (paths.hermes_home / "agents" / "_shared" / "model-routing.json").read_text(encoding="utf-8")
        assert '"required_providers"' in routing
        assert '"alias": "oc"' in routing
        assert '"alias": "mmf"' in routing
    assert _caddy_version_tuple("v2.7.6 h1:test") == (2, 7, 6)
    assert _caddy_version_tuple("2.10.0") == (2, 10, 0)
    caddy_config = CustomerConfig(
        owner_name="Customer",
        owner_work="Work",
        owner_focus="Focus",
        timezone="Asia/Ho_Chi_Minh",
        telegram_chat_id="-1001234567890",
        telegram_bot_token="token",
        telegram_allowed_users="",
        thread_scout="11",
        thread_scribe="12",
        thread_reach="13",
        thread_dev="14",
        telegram_home_channel="telegram:-1001234567890",
        public_ip="203.0.113.10",
        acme_email="owner@example.com",
        dash_user="gooros",
        dash_password="secret",
        model_policy="9router-free-combo-round-robin",
    )
    caddy_block = render_caddy_block(caddy_config, "HASH", auth_directive="basicauth")
    assert "basicauth {" in caddy_block
    assert "header_up Host 127.0.0.1:9119" in caddy_block
    assert "\troute {" in caddy_block
    assert "redir @router_root /dashboard 302" in caddy_block
    assert "{$" not in caddy_block
    service_text = (ROOT / "assets" / "proxy" / "systemd" / "9router.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=%GOOROS_9ROUTER_APP_DIR%" in service_text
    assert "ExecStart=%GOOROS_NODE_BIN% %GOOROS_9ROUTER_SERVER_JS%" in service_text

    class HelpRunner:
        def __init__(self, help_text: str):
            self.help_text = help_text

        def run(self, argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 0, self.help_text, "")

    assert hermes_plugin_enable_command(HelpRunner("--no-allow-tool-override"), "telegram_topic_profiles") == [
        "hermes",
        "plugins",
        "enable",
        "telegram_topic_profiles",
        "--no-allow-tool-override",
    ]
    assert hermes_plugin_enable_command(HelpRunner("usage"), "telegram_topic_profiles") == [
        "hermes",
        "plugins",
        "enable",
        "telegram_topic_profiles",
    ]
    cookie_headers = Message()
    cookie_headers.add_header("Set-Cookie", "auth_token=abc123; Path=/; Secure; HttpOnly")
    assert _auth_cookie_from_headers(cookie_headers) == "auth_token=abc123"
    with tempfile.TemporaryDirectory() as tmp_router:
        package = Path(tmp_router) / "lib" / "node_modules" / "9router"
        (package / "app").mkdir(parents=True)
        server_js = package / "app" / "server.js"
        server_js.write_text("// server\n", encoding="utf-8")
        cli_js = package / "cli.js"
        cli_js.write_text("// cli\n", encoding="utf-8")
        assert resolve_9router_server_js(HelpRunner(""), str(cli_js)) == server_js.resolve()

    class GatewayRunner:
        dry_run = False

        def __init__(self):
            self.commands = []

        def log(self, _message):
            return None

        def run(self, argv, **_kwargs):
            self.commands.append(argv)
            if argv[:4] == ["hermes", "gateway", "--accept-hooks", "restart"]:
                return subprocess.CompletedProcess(argv, 1, "", "service not installed")
            return subprocess.CompletedProcess(argv, 0, "ok", "")

    gateway_runner = GatewayRunner()
    restart_gateway(gateway_runner)
    assert ["hermes", "gateway", "--accept-hooks", "install", "--start-now", "--start-on-login"] in gateway_runner.commands
    assert ["hermes", "gateway", "--accept-hooks", "start", "--all"] in gateway_runner.commands
    print("smoke ok")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
