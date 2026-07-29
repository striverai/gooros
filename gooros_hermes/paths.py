from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _expand(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class InstallPaths:
    hermes_home: Path
    project_dir: Path
    config_dir: Path
    data_dir: Path

    @property
    def customer_config(self) -> Path:
        return self.config_dir / "customer.yaml"

    @property
    def secrets_env(self) -> Path:
        return self.config_dir / "secrets.env"

    @property
    def install_state(self) -> Path:
        return self.project_dir / ".gooros" / "installed.json"

    @property
    def ownership_map(self) -> Path:
        return self.project_dir / ".gooros" / "ownership.json"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def release_dir(self) -> Path:
        return self.data_dir / "releases"

    @property
    def current_release(self) -> Path:
        return self.data_dir / "current"


def default_paths(
    hermes_home: str | None = None,
    project_dir: str | None = None,
    config_dir: str | None = None,
    data_dir: str | None = None,
) -> InstallPaths:
    return InstallPaths(
        hermes_home=_expand(hermes_home or os.environ.get("HERMES_HOME", "~/.hermes")),
        project_dir=_expand(project_dir or "~/agent-mission-control"),
        config_dir=_expand(config_dir or "~/.config/gooros/hermes-mission-control"),
        data_dir=_expand(data_dir or "~/.local/share/gooros/hermes-mission-control"),
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def asset_path(*parts: str) -> Path:
    return repo_root() / "assets" / Path(*parts)

