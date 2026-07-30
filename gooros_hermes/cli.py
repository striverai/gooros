from __future__ import annotations

import argparse
import json
import sys

from .configstore import collect_customer_config
from .installer import activate_prompt16_multi_agent_mode, install, install_prompt15_routing_plugin, verify_prompt17_telegram_routing_audit_live
from .paths import default_paths
from .prompt15 import prompt15_report_markdown_path
from .prompt16 import prompt16_report_markdown_path
from .prompt17 import prompt17_report_markdown_path
from .prompt19 import discover_prompt19_sources, render_prompt19_markdown
from .rollback import rollback
from .runner import Runner
from .tailscale import prompt33_report_markdown_path, setup_tailscale_dashboard
from .updater import cmd_apply_staged, update
from .verify import doctor, print_doctor, verify_install


def safe_print(message: str, *, file=None) -> None:
    target = file or sys.stdout
    try:
        print(message, file=target)
    except UnicodeEncodeError:
        encoding = target.encoding or "utf-8"
        safe = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, file=target)


def add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hermes-home")
    parser.add_argument("--project-dir")
    parser.add_argument("--config-dir")
    parser.add_argument("--data-dir")


def add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", help="Read customer install values from a local KEY=VALUE file")
    parser.add_argument("--owner-name")
    parser.add_argument("--owner-work", default="")
    parser.add_argument("--owner-focus", default="")
    parser.add_argument("--owner-working-hours", default="")
    parser.add_argument("--owner-important-people", default="")
    parser.add_argument("--owner-cares-about", default="")
    parser.add_argument("--timezone", default="Asia/Ho_Chi_Minh")
    parser.add_argument("--telegram-chat-id")
    parser.add_argument("--telegram-bot-token")
    parser.add_argument("--telegram-allowed-users", default="")
    parser.add_argument("--thread-command")
    parser.add_argument("--thread-scout")
    parser.add_argument("--thread-scribe")
    parser.add_argument("--thread-reach")
    parser.add_argument("--thread-dev")
    parser.add_argument("--telegram-home-channel", default="")
    parser.add_argument("--public-ip")
    parser.add_argument("--acme-email")
    parser.add_argument("--dash-user", default="gooros")
    parser.add_argument("--dash-password")
    parser.add_argument("--model-policy", default="9router-free-combo-round-robin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gooros-hermes")
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{install,plan,prompt15-plugin,prompt16-activate,prompt17-audit,prompt19-discover,tailscale-serve,verify,doctor,update,rollback,expose,auth}",
    )

    install_p = sub.add_parser("install", help="Install Gooros Hermes Mission Control")
    add_common_paths(install_p)
    add_config_args(install_p)
    install_p.add_argument("--yes", "-y", action="store_true", help="Non-interactive; fail if required fields are missing")
    install_p.add_argument("--dry-run", action="store_true")
    install_p.add_argument("--with-hermes", action="store_true", help="Install Hermes if missing")
    install_p.add_argument("--with-9router", action="store_true")
    install_p.add_argument("--with-public-dashboards", action="store_true")
    install_p.add_argument("--domain-mode", choices=["sslip"], default="sslip")
    install_p.add_argument("--auth", choices=["basic"], default="basic")
    install_p.add_argument("--systemd", action="store_true", help="Install systemd services")

    plan_p = sub.add_parser("plan", help="Print install/update plan without mutating")
    add_common_paths(plan_p)
    add_config_args(plan_p)
    plan_p.add_argument("--with-hermes", action="store_true")
    plan_p.add_argument("--with-9router", action="store_true")
    plan_p.add_argument("--with-public-dashboards", action="store_true")
    plan_p.add_argument("--domain-mode", choices=["sslip"], default="sslip")
    plan_p.add_argument("--auth", choices=["basic"], default="basic")
    plan_p.add_argument("--systemd", action="store_true")
    plan_p.add_argument("--yes", action="store_true", default=True)
    plan_p.add_argument("--dry-run", action="store_true", default=True)

    prompt15_p = sub.add_parser("prompt15-plugin", help="Create the exact Prompt 15 Telegram topic routing plugin without enabling or restarting")
    add_common_paths(prompt15_p)
    add_config_args(prompt15_p)
    prompt15_p.add_argument("--yes", "-y", action="store_true", help="Non-interactive; fail if required thread IDs are missing")
    prompt15_p.add_argument("--dry-run", action="store_true")

    prompt16_p = sub.add_parser("prompt16-activate", help="Enable Telegram topic routing, multiplex profiles, and restart Hermes gateway")
    add_common_paths(prompt16_p)
    prompt16_p.add_argument("--dry-run", action="store_true")

    prompt17_p = sub.add_parser("prompt17-audit", help="Audit Telegram topic routing against Prompt 17")
    add_common_paths(prompt17_p)
    add_config_args(prompt17_p)
    prompt17_p.add_argument("--yes", "-y", action="store_true", help="Non-interactive; fail if required routing IDs are missing")
    prompt17_p.add_argument("--dry-run", action="store_true")

    prompt19_p = sub.add_parser("prompt19-discover", help="Read Hermes state.db, kanban.db, and gateway_state.json schema without writing files")
    add_common_paths(prompt19_p)
    prompt19_p.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of Markdown")

    tailscale_p = sub.add_parser("tailscale-serve", help="Expose Mission Control privately over Tailscale Serve")
    add_common_paths(tailscale_p)
    tailscale_p.add_argument("--dry-run", action="store_true")
    tailscale_p.add_argument("--port", type=int, default=51763)

    verify_p = sub.add_parser("verify", help="Verify an installed system")
    add_common_paths(verify_p)
    verify_p.add_argument("--public", action="store_true")
    verify_p.add_argument("--with-9router", action="store_true")

    doctor_p = sub.add_parser("doctor", help="Inspect current machine without mutating")
    add_common_paths(doctor_p)

    update_p = sub.add_parser("update", help="Fetch, stage, and upgrade to the latest safe release")
    add_common_paths(update_p)
    update_p.add_argument("--repo-url", help="Git release repo URL; normally read from install state")
    update_p.add_argument("--target", help="Git tag/ref/commit to install; default: latest tag")
    update_p.add_argument("--source-dir", help="Use a local source checkout instead of Git; for release testing")
    update_p.add_argument("--plan", action="store_true", help="Show the update plan without changing runtime files")
    update_p.add_argument("--dry-run", action="store_true", help="Alias for --plan")
    update_p.add_argument("--force", action="store_true", help="Reapply even when the same revision is installed")
    update_p.add_argument("--allow-downgrade", action="store_true")
    update_p.add_argument("--skip-verify", action="store_true", help="Skip post-update health checks; not for customer releases")
    update_p.add_argument("--no-reinstall-cli", action="store_true", help="Do not reinstall the CLI from the updated repo cache")
    update_p.add_argument("--public", action="store_true", help="Force public dashboard module on")
    update_p.add_argument("--no-public", action="store_true", help="Force public dashboard module off")
    update_p.add_argument("--with-9router", action="store_true")
    update_p.add_argument("--no-9router", action="store_true")
    update_p.add_argument("--systemd", action="store_true")
    update_p.add_argument("--no-systemd", action="store_true")

    rollback_p = sub.add_parser("rollback", help="Restore the latest safe snapshot")
    add_common_paths(rollback_p)
    rollback_p.add_argument("--snapshot", help="Snapshot directory to restore; default: latest")
    rollback_p.add_argument("--yes", "-y", action="store_true")
    rollback_p.add_argument("--restore-data", action="store_true", help="Also restore customer DB/content data from the snapshot")
    rollback_p.add_argument("--public", action="store_true")
    rollback_p.add_argument("--skip-verify", action="store_true")

    apply_p = sub.add_parser("_apply-staged", help=argparse.SUPPRESS)
    add_common_paths(apply_p)
    apply_p.add_argument("--staged-dir", required=True)
    apply_p.add_argument("--snapshot", required=True)
    apply_p.add_argument("--release-id", required=True)
    apply_p.add_argument("--source-revision", required=True)
    apply_p.add_argument("--target-ref", required=True)
    apply_p.add_argument("--repo-url", default="")
    apply_p.add_argument("--force", action="store_true")
    apply_p.add_argument("--skip-verify", action="store_true")
    apply_p.add_argument("--public", action="store_true")
    apply_p.add_argument("--no-public", action="store_true")
    apply_p.add_argument("--with-9router", action="store_true")
    apply_p.add_argument("--no-9router", action="store_true")
    apply_p.add_argument("--systemd", action="store_true")
    apply_p.add_argument("--no-systemd", action="store_true")
    sub._choices_actions = [action for action in sub._choices_actions if action.dest != "_apply-staged"]

    expose = sub.add_parser("expose", help="Public dashboard helper")
    expose_sub = expose.add_subparsers(dest="expose_command", required=True)
    expose_sub.add_parser("verify", help="Verify public dashboard exposure")

    auth = sub.add_parser("auth", help="Auth helper")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_sub.add_parser("rotate", help="Rotate dashboard auth password (not implemented yet)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"install", "plan"}:
            return install(args)
        if args.command == "prompt15-plugin":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            config = collect_customer_config(args, interactive=not args.yes and not args.dry_run)
            required = ("telegram_chat_id", "thread_scout", "thread_scribe", "thread_reach", "thread_dev")
            missing = [key for key in required if not getattr(config, key)]
            if missing and not args.dry_run:
                raise RuntimeError("missing required Prompt 15 config: " + ", ".join(missing))
            install_prompt15_routing_plugin(Runner(dry_run=args.dry_run, verbose=True), paths, config)
            if not args.dry_run:
                safe_print(f"Prompt 15 routing plugin report: {prompt15_report_markdown_path(paths.project_dir)}")
            return 0
        if args.command == "prompt16-activate":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            activate_prompt16_multi_agent_mode(Runner(dry_run=args.dry_run, verbose=True), paths)
            if not args.dry_run:
                safe_print(f"Prompt 16 multi-agent activation report: {prompt16_report_markdown_path(paths.project_dir)}")
            return 0
        if args.command == "prompt17-audit":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            config = collect_customer_config(args, interactive=not args.yes and not args.dry_run)
            required = ("telegram_chat_id", "thread_command", "thread_scout", "thread_scribe", "thread_reach", "thread_dev")
            missing = [key for key in required if not getattr(config, key)]
            if missing and not args.dry_run:
                raise RuntimeError("missing required Prompt 17 config: " + ", ".join(missing))
            verify_prompt17_telegram_routing_audit_live(Runner(dry_run=args.dry_run, verbose=True), paths, config)
            if not args.dry_run:
                safe_print(f"Prompt 17 Telegram routing audit report: {prompt17_report_markdown_path(paths.project_dir)}")
            return 0
        if args.command == "prompt19-discover":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            report = discover_prompt19_sources(paths.hermes_home)
            if args.json:
                safe_print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                safe_print(render_prompt19_markdown(report))
            return 0
        if args.command == "tailscale-serve":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            report = setup_tailscale_dashboard(Runner(dry_run=args.dry_run, verbose=True), paths, port=args.port)
            safe_print(json.dumps(report, ensure_ascii=False, indent=2))
            if not args.dry_run:
                safe_print(f"Prompt 33 Tailscale report: {prompt33_report_markdown_path(paths.project_dir)}")
            return 0 if report.get("status") == "passed" else 1
        if args.command == "verify":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            failures = verify_install(paths, public=args.public, with_9router=args.with_9router)
            if failures:
                for failure in failures:
                    safe_print(f"FAIL: {failure}")
                return 1
            safe_print("OK")
            return 0
        if args.command == "doctor":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            print_doctor(doctor(paths, Runner()))
            return 0
        if args.command == "update":
            return update(args)
        if args.command == "rollback":
            return rollback(args)
        if args.command == "_apply-staged":
            return cmd_apply_staged(args)
        if args.command == "expose":
            print("expose verify is available after install via Caddy/curl checks in verify --public.")
            return 0
        if args.command == "auth":
            print("auth rotate scaffolded; do not expose dashboards without stored Caddy hash and a tested rotation flow.")
            return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        safe_print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
