from __future__ import annotations

import compileall
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gooros_hermes.dashboard_patcher import build_live_dashboard
from gooros_hermes.release import read_release_manifest, validate_release_manifest


def main() -> int:
    ok = compileall.compile_dir(ROOT / "gooros_hermes", quiet=1)
    ok = compileall.compile_dir(ROOT / "migrations", quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "dashboard" / "server.py"), quiet=1) and ok
    validate_release_manifest(ROOT, read_release_manifest(ROOT))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "index.html"
        build_live_dashboard(ROOT / "assets" / "dashboard" / "template.html", out)
        text = out.read_text(encoding="utf-8")
        assert "DEMO_" not in text
        assert "hard-coded reply" not in text
        assert "hydrate(); connectSSE(); startPolling();" in text
    print("smoke ok")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
