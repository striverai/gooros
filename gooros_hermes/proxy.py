from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .configstore import CustomerConfig
from .fsutil import atomic_write_text, ensure_dir
from .paths import InstallPaths, asset_path
from .runner import Runner

BEGIN = "# BEGIN GOOROS HERMES"
END = "# END GOOROS HERMES"


def sslip_name(label: str, public_ip: str) -> str:
    host_ip = public_ip.strip()
    if ":" in host_ip:
        host_ip = host_ip.replace(":", "-")
    return f"{label}.{host_ip}.sslip.io"


def root_prefix() -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    sudo = shutil.which("sudo")
    if sudo:
        return [sudo]
    raise RuntimeError("root or sudo is required for Caddy/systemd operations")


def install_caddy_if_missing(runner: Runner) -> None:
    if runner.dry_run:
        runner.log("would ensure Caddy is installed")
        return
    if shutil.which("caddy"):
        return
    if not shutil.which("apt-get"):
        raise RuntimeError("caddy not found; install Caddy first or use a Debian/Ubuntu VPS")
    prefix = root_prefix()
    runner.run(prefix + ["apt-get", "update"], timeout=300)
    runner.run(prefix + ["apt-get", "install", "-y", "debian-keyring", "debian-archive-keyring", "apt-transport-https", "curl", "gnupg"], timeout=300)
    runner.shell("curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg", timeout=300)
    runner.shell("curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null", timeout=300)
    runner.run(prefix + ["apt-get", "update"], timeout=300)
    runner.run(prefix + ["apt-get", "install", "-y", "caddy"], timeout=300)


def caddy_hash_password(runner: Runner, password: str) -> str:
    if runner.dry_run:
        return "DRY_RUN_HASH"
    result = runner.run(["caddy", "hash-password", "--plaintext", password], capture=True, timeout=30)
    return result.stdout.strip()


def render_caddy_block(config: CustomerConfig, pass_hash: str) -> str:
    template = asset_path("proxy", "Caddyfile.tmpl").read_text(encoding="utf-8")
    values = {
        "GOOROS_ACME_EMAIL": config.acme_email,
        "GOOROS_DASH_USER": config.dash_user,
        "GOOROS_DASH_PASS_HASH": pass_hash,
        "GOOROS_MISSION_HOST": sslip_name("mission", config.public_ip),
        "GOOROS_HERMES_HOST": sslip_name("hermes", config.public_ip),
        "GOOROS_ROUTER_HOST": sslip_name("router", config.public_ip),
    }
    for key, value in values.items():
        template = template.replace("{$" + key + "}", value)
    return f"{BEGIN}\n{template.rstrip()}\n{END}\n"


def merge_managed_block(existing: str, block: str) -> str:
    if BEGIN in existing and END in existing:
        before = existing.split(BEGIN, 1)[0].rstrip()
        after = existing.split(END, 1)[1].lstrip()
        return (before + "\n\n" if before else "") + block + ("\n" + after if after else "")
    return existing.rstrip() + "\n\n" + block if existing.strip() else block


def install_public_proxy(runner: Runner, paths: InstallPaths, config: CustomerConfig, pass_hash: str) -> None:
    install_caddy_if_missing(runner)
    block = render_caddy_block(config, pass_hash)
    caddyfile = Path("/etc/caddy/Caddyfile")
    if runner.dry_run:
        runner.log(f"would merge managed Caddy block into {caddyfile}")
        runner.log(block)
        return
    existing = caddyfile.read_text(encoding="utf-8") if caddyfile.exists() else ""
    merged = merge_managed_block(existing, block)
    tmp = Path("/tmp/gooros-caddyfile")
    atomic_write_text(tmp, merged)
    prefix = root_prefix()
    runner.run(prefix + ["install", "-m", "0644", str(tmp), str(caddyfile)])
    runner.run(prefix + ["caddy", "validate", "--config", str(caddyfile)], timeout=30)
    runner.run(prefix + ["systemctl", "enable", "--now", "caddy"], timeout=60)
    runner.run(prefix + ["systemctl", "reload", "caddy"], timeout=60, check=False)


def write_system_env(runner: Runner, paths: InstallPaths, config: CustomerConfig, pass_hash: str) -> None:
    env_text = "\n".join(
        [
            f"HERMES_HOME={paths.hermes_home}",
            f"PROJECT_DIR={paths.project_dir}",
            f"AGENT_LOG_DB={paths.project_dir / 'agent-logs.db'}",
            f"BOARD_DB={paths.project_dir / 'board.db'}",
            f"CONTENT_DIR={paths.project_dir / 'content'}",
            f"TELEGRAM_HOME_CHANNEL={config.telegram_home_channel}",
            f"GOOROS_ACME_EMAIL={config.acme_email}",
            f"GOOROS_DASH_USER={config.dash_user}",
            f"GOOROS_DASH_PASS_HASH={pass_hash}",
            f"GOOROS_MISSION_HOST={sslip_name('mission', config.public_ip)}",
            f"GOOROS_HERMES_HOST={sslip_name('hermes', config.public_ip)}",
            f"GOOROS_ROUTER_HOST={sslip_name('router', config.public_ip)}",
        ]
    ) + "\n"
    if runner.dry_run:
        runner.log("would write /etc/gooros/hermes-mission-control.env")
        return
    tmp = Path("/tmp/gooros-hermes-mission-control.env")
    atomic_write_text(tmp, env_text, mode=0o600)
    prefix = root_prefix()
    runner.run(prefix + ["install", "-d", "-m", "0750", "/etc/gooros"])
    runner.run(prefix + ["install", "-m", "0600", str(tmp), "/etc/gooros/hermes-mission-control.env"])


def verify_public_proxy(runner: Runner, config: CustomerConfig) -> list[str]:
    failures: list[str] = []
    for label in ("mission", "hermes", "router"):
        host = sslip_name(label, config.public_ip)
        result = runner.run(["curl", "-k", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"https://{host}"], capture=True, check=False, timeout=20)
        code = (result.stdout or "").strip()
        if code != "401":
            failures.append(f"{host} without credentials returned {code}, expected 401")
    return failures
