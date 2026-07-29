from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from .constants import PRODUCT
from .fsutil import read_json

MANIFEST_REL = Path("manifests") / "release.json"

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "dryrun-artifacts"}
SECRET_NAMES = {".env", "secrets.env", "id_rsa", "id_dsa", "id_ed25519", "known_hosts"}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk")


def read_release_manifest(source_root: Path) -> dict[str, object]:
    data = read_json(source_root / MANIFEST_REL)
    if not isinstance(data, dict):
        raise RuntimeError(f"release manifest missing or invalid: {source_root / MANIFEST_REL}")
    return data


def _validate_relative_path(value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"{field} contains an empty/non-string path"
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return f"{field} contains unsafe path: {value}"
    return None


def validate_release_manifest(source_root: Path, manifest: dict[str, object]) -> None:
    errors: list[str] = []
    if manifest.get("product") != PRODUCT:
        errors.append(f"product mismatch: expected {PRODUCT}, got {manifest.get('product')}")
    if not isinstance(manifest.get("version"), str) or not str(manifest.get("version")).strip():
        errors.append("version must be a non-empty string")
    if not isinstance(manifest.get("schema_version"), int):
        errors.append("schema_version must be an integer")
    for key in ("migrations", "release_owned", "customer_owned", "merge_managed"):
        if not isinstance(manifest.get(key), list):
            errors.append(f"{key} must be a list")
    for key in ("migrations", "release_owned"):
        for item in manifest.get(key, []) if isinstance(manifest.get(key), list) else []:
            error = _validate_relative_path(item, key)
            if error:
                errors.append(error)
    for rel in manifest.get("release_owned", []) if isinstance(manifest.get("release_owned"), list) else []:
        if isinstance(rel, str) and not (source_root / rel).exists():
            errors.append(f"release_owned path does not exist in source: {rel}")
    if errors:
        raise RuntimeError("release manifest failed validation:\n- " + "\n- ".join(errors))


def release_version(manifest: dict[str, object]) -> str:
    return str(manifest["version"])


def migration_ids(manifest: dict[str, object]) -> list[str]:
    values = manifest.get("migrations", [])
    return [str(value) for value in values] if isinstance(values, list) else []


def version_tuple(version: str) -> tuple[int, ...]:
    text = version.strip().lstrip("vV")
    main = re.split(r"[-+]", text, maxsplit=1)[0]
    parts = []
    for part in main.split("."):
        if part.isdigit():
            parts.append(int(part))
        else:
            match = re.match(r"(\d+)", part)
            parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def compare_versions(left: str, right: str) -> int:
    a = version_tuple(left)
    b = version_tuple(right)
    return (a > b) - (a < b)


def sanitize_release_id(version: str, revision: str | None = None) -> str:
    suffix = f"-{revision[:12]}" if revision else ""
    raw = f"{version}{suffix}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-") or "release"


def scan_for_secret_paths(source_root: Path) -> list[str]:
    findings: list[str] = []
    for path in source_root.rglob("*"):
        rel_parts = set(path.relative_to(source_root).parts)
        if rel_parts & SKIP_DIRS:
            continue
        name = path.name
        lower = name.lower()
        if lower in SECRET_NAMES or lower.endswith(SECRET_SUFFIXES):
            findings.append(str(path.relative_to(source_root)))
    return findings


def tree_checksum(source_root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(paths):
        root = source_root / rel
        if not root.exists():
            continue
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for file_path in files:
            if set(file_path.relative_to(source_root).parts) & SKIP_DIRS:
                continue
            rel_name = file_path.relative_to(source_root).as_posix()
            digest.update(rel_name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def current_source_metadata(source_root: Path | None = None) -> dict[str, str]:
    root = source_root or Path(__file__).resolve().parents[1]

    def git_output(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else ""

    top = git_output("rev-parse", "--show-toplevel")
    if not top:
        return {"source_path": str(root)}
    return {
        "source_path": top,
        "release_repo": git_output("config", "--get", "remote.origin.url"),
        "release_revision": git_output("rev-parse", "HEAD"),
        "release_ref": git_output("rev-parse", "--abbrev-ref", "HEAD"),
    }
