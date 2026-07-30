from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .fsutil import atomic_write_json, atomic_write_text, ensure_dir
from .paths import InstallPaths
from .proxy import root_prefix
from .runner import Runner


PROMPT33_REPORT_JSON = "prompt33-tailscale-verification.json"
PROMPT33_REPORT_MARKDOWN = "prompt33-tailscale-verification.md"
TAILSCALE_LOGIN_RE = re.compile(r"https://login\.tailscale\.com/[^\s]+")


def prompt33_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt33_report_json_path(project_dir: Path) -> Path:
    return prompt33_report_dir(project_dir) / PROMPT33_REPORT_JSON


def prompt33_report_markdown_path(project_dir: Path) -> Path:
    return prompt33_report_dir(project_dir) / PROMPT33_REPORT_MARKDOWN


def _combined(result) -> str:
    return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()


def extract_login_url(text: str) -> str:
    match = TAILSCALE_LOGIN_RE.search(text or "")
    return match.group(0) if match else ""


def parse_tailscale_status_json(text: str) -> dict:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    self_info = data.get("Self") if isinstance(data, dict) else {}
    if not isinstance(self_info, dict):
        self_info = {}
    tailscale_ips = self_info.get("TailscaleIPs") or []
    ip4 = next((str(ip) for ip in tailscale_ips if str(ip).startswith("100.")), "")
    dns_name = str(self_info.get("DNSName") or "").rstrip(".")
    host_name = str(self_info.get("HostName") or "")
    return {
        "backend_state": data.get("BackendState", "") if isinstance(data, dict) else "",
        "hostname": dns_name or host_name,
        "ip4": ip4,
        "raw_self": self_info,
    }


def install_tailscale_if_missing(runner: Runner) -> None:
    if shutil.which("tailscale"):
        return
    if runner.dry_run:
        runner.log("would install Tailscale with the official install script")
        return
    if not shutil.which("curl"):
        raise RuntimeError("curl is required to install Tailscale")
    prefix = root_prefix()
    sudo = "sudo " if prefix else ""
    runner.shell(f"curl -fsSL https://tailscale.com/install.sh | {sudo}sh", timeout=600)


def ensure_tailscale_up(runner: Runner) -> tuple[bool, str]:
    status = runner.run(["tailscale", "status", "--json"], capture=True, check=False, timeout=20)
    parsed = parse_tailscale_status_json(status.stdout or "")
    if status.returncode == 0 and parsed.get("backend_state") == "Running":
        return True, ""
    up = runner.run(["tailscale", "up", "--accept-routes=false", "--ssh=false"], capture=True, check=False, timeout=60)
    output = _combined(up)
    login_url = extract_login_url(output)
    return up.returncode == 0, login_url


def configure_tailscale_serve(runner: Runner, *, port: int = 51763) -> dict:
    target = f"http://127.0.0.1:{port}"
    result = runner.run(["tailscale", "serve", "--bg", "--https=443", target], capture=True, check=False, timeout=30)
    if result.returncode != 0:
        return {"ok": False, "command": ["tailscale", "serve", "--bg", "--https=443", target], "output": _combined(result)}
    status = runner.run(["tailscale", "serve", "status"], capture=True, check=False, timeout=20)
    return {
        "ok": status.returncode == 0,
        "command": ["tailscale", "serve", "--bg", "--https=443", target],
        "target": target,
        "serve_status": _combined(status),
    }


def verify_no_funnel(runner: Runner) -> dict:
    status = runner.run(["tailscale", "funnel", "status"], capture=True, check=False, timeout=20)
    text = _combined(status)
    active = status.returncode == 0 and "https://" in text and "off" not in text.lower()
    return {"funnel_active": active, "status": text}


def write_prompt33_report(paths: InstallPaths, report: dict) -> None:
    ensure_dir(prompt33_report_dir(paths.project_dir))
    atomic_write_json(prompt33_report_json_path(paths.project_dir), report, mode=0o600)
    lines = [
        "# Prompt 33 - Tailscale Remote Access Verification",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Login URL: {report.get('login_url') or '(already authenticated or unavailable)'}",
        f"- Hostname: {report.get('hostname') or '(unknown)'}",
        f"- Tailscale IPv4: {report.get('ip4') or '(unknown)'}",
        f"- Tailnet HTTPS: {report.get('https_url') or '(unknown until authenticated)'}",
        f"- Serve target: {report.get('serve', {}).get('target', 'http://127.0.0.1:51763')}",
        f"- Funnel active: {report.get('funnel', {}).get('funnel_active', False)}",
        "",
        "Dashboard server remains bound to 127.0.0.1; remote access is via tailscale serve only.",
    ]
    atomic_write_text(prompt33_report_markdown_path(paths.project_dir), "\n".join(lines) + "\n", mode=0o600)


def setup_tailscale_dashboard(runner: Runner, paths: InstallPaths, *, port: int = 51763) -> dict:
    install_tailscale_if_missing(runner)
    up_ok, login_url = ensure_tailscale_up(runner)
    status = runner.run(["tailscale", "status", "--json"], capture=True, check=False, timeout=20)
    parsed = parse_tailscale_status_json(status.stdout or "")
    serve = configure_tailscale_serve(runner, port=port) if up_ok or runner.dry_run else {"ok": False, "target": f"http://127.0.0.1:{port}"}
    funnel = verify_no_funnel(runner) if up_ok or runner.dry_run else {"funnel_active": False, "status": "not checked before auth"}
    hostname = parsed.get("hostname", "")
    report = {
        "prompt": "Prompt 33",
        "status": "passed" if (up_ok and serve.get("ok") and not funnel.get("funnel_active")) or runner.dry_run else "needs_auth",
        "login_url": login_url,
        "hostname": hostname,
        "ip4": parsed.get("ip4", ""),
        "https_url": f"https://{hostname}" if hostname else "",
        "serve": serve,
        "funnel": funnel,
        "bind": "127.0.0.1",
        "public_internet_exposure": "not configured by Gooros; do not use tailscale funnel",
    }
    if not runner.dry_run:
        write_prompt33_report(paths, report)
    return report


def validate_prompt33_source(server_text: str, cli_text: str, tailscale_text: str) -> list[str]:
    failures: list[str] = []
    if 'HOST = "127.0.0.1"' not in server_text:
        failures.append("Prompt 33 requires Mission Control to bind only 127.0.0.1")
    for token in ('"tailscale", "serve", "--bg", "--https=443"', "http://127.0.0.1:"):
        if token not in tailscale_text:
            failures.append(f"Prompt 33 Tailscale serve support missing: {token}")
    enables_funnel = re.search(r"runner\.run\(\s*\[\s*['\"]tailscale['\"]\s*,\s*['\"]funnel['\"]\s*,\s*['\"](?:on|enable)['\"]", tailscale_text)
    if "tailscale funnel" in cli_text.lower() or enables_funnel:
        failures.append("Prompt 33 must not enable tailscale funnel")
    if "tailscale-serve" not in cli_text:
        failures.append("Prompt 33 CLI command missing: tailscale-serve")
    return failures
