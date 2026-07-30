from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Runner:
    dry_run: bool = False
    verbose: bool = True
    env: dict[str, str] = field(default_factory=dict)

    def log(self, message: str) -> None:
        if self.verbose:
            try:
                print(message, flush=True)
            except UnicodeEncodeError:
                encoding = sys.stdout.encoding or "utf-8"
                safe = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
                print(safe, flush=True)

    def command_text(self, argv: list[str]) -> str:
        return " ".join(shlex.quote(x) for x in argv)

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
        capture: bool = False,
        timeout: int | None = None,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.log(f"$ {self.command_text(argv)}")
        if self.dry_run:
            return subprocess.CompletedProcess(argv, 0, "", "")
        merged_env = os.environ.copy()
        merged_env.update(self.env)
        if env:
            merged_env.update(env)
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=input_text,
            capture_output=capture,
            timeout=timeout,
            env=merged_env,
        )
        if check and result.returncode != 0:
            detail = result.stderr or result.stdout or ""
            raise RuntimeError(f"command failed ({result.returncode}): {self.command_text(argv)}\n{detail}")
        return result

    def shell(
        self,
        script: str,
        *,
        check: bool = True,
        capture: bool = False,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(["bash", "-lc", script], check=check, capture=capture, timeout=timeout, env=env)
