from __future__ import annotations

import argparse
import sys

from .installer import install
from .paths import default_paths
from .rollback import rollback
from .runner import Runner
from .updater import cmd_apply_staged, update
from .verify import doctor, print_doctor, verify_install


def add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hermes-home")
    parser.add_argument("--project-dir")
    parser.add_argument("--config-dir")
    parser.add_argument("--data-dir")


def add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner-name")
    parser.add_argument("--owner-work", default="")
    parser.add_argument("--owner-focus", default="")
    parser.add_argument("--timezone", default="Asia/Ho_Chi_Minh")
    parser.add_argument("--telegram-chat-id")
    parser.add_argument("--thread-scout")
    parser.add_argument("--thread-scribe")
    parser.add_argument("--thread-reach")
    parser.add_argument("--thread-dev")
    parser.add_argument("--telegram-home-channel", default="")
    parser.add_argument("--public-ip")
    parser.add_argument("--acme-email")
    parser.add_argument("--dash-user", default="gooros")
    parser.add_argument("--dash-password")
    parser.add_argument("--model-policy", default="deepseek-free-first")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gooros-hermes")
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{install,plan,verify,doctor,update,rollback,expose,auth}",
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

    verify_p = sub.add_parser("verify", help="Verify an installed system")
    add_common_paths(verify_p)
    verify_p.add_argument("--public", action="store_true")

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
        if args.command == "verify":
            paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
            failures = verify_install(paths, public=args.public)
            if failures:
                for failure in failures:
                    print(f"FAIL: {failure}")
                return 1
            print("OK")
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
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
