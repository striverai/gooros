#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("GOOROS_MODEL_ROUTING", "~/.hermes/agents/_shared/model-routing.json")).expanduser()


def main() -> int:
    task = " ".join(sys.argv[1:]).lower()
    fallback = os.environ.get("GOOROS_DEFAULT_MODEL", "").strip()
    if not CONFIG.exists():
        print(fallback)
        return 0
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    models = data.get("models", [])
    if not models:
        print(fallback)
        return 0
    complex_words = data.get("complex_keywords", ["code", "debug", "architecture", "plan", "strategy", "reason", "multi-step", "longform"])
    simple_words = data.get("simple_keywords", ["summary", "format", "short", "quick", "rewrite", "caption"])
    premium = next((m["id"] for m in models if m.get("tier") == "premium"), models[0]["id"] if models else "")
    fast = next((m["id"] for m in models if m.get("tier") == "fast"), premium)
    if any(w in task for w in complex_words):
        print(premium)
    elif any(w in task for w in simple_words):
        print(fast)
    else:
        print(premium)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
