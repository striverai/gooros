#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


AGENTS = {"orchestrator", "scout", "scribe", "reach", "dev"}
DELIMITER = "\n§\n"
SCOPE_KEYWORDS = {
    "orchestrator": ("owner", "chủ sở hữu", "chu so huu", "quyết định", "quyet dinh", "điều phối", "dieu phoi", "blocker", "kết quả", "ket qua"),
    "scout": ("nguồn", "nguon", "source", "research", "nghiên cứu", "nghien cuu", "xu hướng", "xu huong", "đối thủ", "doi thu", "thị trường", "thi truong"),
    "scribe": ("draft", "bài viết", "bai viet", "content", "nội dung", "noi dung", "caption", "newsletter", "title", "meta", "cta", "voice"),
    "reach": ("marketing", "growth", "tăng trưởng", "tang truong", "kiếm tiền", "kiem tien", "monetization", "campaign", "affiliate", "kpi", "launch"),
    "dev": ("code", "bug", "test", "deploy", "dashboard", "api", "script", "automation", "tự động hóa", "tu dong hoa", "tích hợp", "tich hop"),
}


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.casefold()).strip()


def relevant(agent: str, text: str) -> bool:
    normalized = normalize(text)
    shared = ("owner", "chu so huu", "chủ sở hữu", "gooros", "hermes", "telegram", "workspace", "session", "prompt")
    return any(normalize(keyword) in normalized for keyword in (*shared, *SCOPE_KEYWORDS[agent]))


def memory_path(agent: str) -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()
    root = hermes_home if agent == "orchestrator" else hermes_home / "profiles" / agent
    return root / "memories" / "MEMORY.md"


def append_memory(agent: str, text: str) -> Path:
    path = memory_path(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entry = f"<!-- GOOROS-HERMES-MANAGED: {agent} scoped memory Prompt 6 {stamp} -->\n{text.strip()}"
    path.write_text((entry + (DELIMITER + old if old else "")).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) < 2:
        print("usage: append-agent-memory.py <agent> <memory text>", file=sys.stderr)
        return 2
    agent = args[0].strip().lower()
    text = " ".join(args[1:]).strip()
    if agent not in AGENTS:
        print(f"unsupported agent: {agent}", file=sys.stderr)
        return 2
    if not relevant(agent, text):
        print(f"refusing out-of-scope memory for {agent}; route it to the correct teammate first", file=sys.stderr)
        return 3
    path = append_memory(agent, text)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
