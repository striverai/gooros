from __future__ import annotations

import compileall
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gooros_hermes.dashboard_patcher import build_live_dashboard
from gooros_hermes.installer import choose_9router_model
from gooros_hermes.release import read_release_manifest, validate_release_manifest
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
    validate_release_manifest(ROOT, read_release_manifest(ROOT))
    assert __version__ == VERSION
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
    print("smoke ok")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
