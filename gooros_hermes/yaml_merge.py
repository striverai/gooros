from __future__ import annotations

from pathlib import Path


def _find_block(lines: list[str], key: str, parent_start: int = 0, parent_indent: int = -1) -> tuple[int, int] | None:
    target = f"{key}:"
    start = None
    indent = None
    for i in range(parent_start, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cur_indent = len(raw) - len(raw.lstrip(" "))
        if parent_indent >= 0 and cur_indent <= parent_indent and i > parent_start:
            break
        if stripped.startswith(target):
            start = i
            indent = cur_indent
            break
    if start is None or indent is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped or stripped.startswith("#"):
            continue
        cur_indent = len(lines[j]) - len(lines[j].lstrip(" "))
        if cur_indent <= indent:
            end = j
            break
    return start, end


def remove_top_level_block(path: Path, key: str) -> bool:
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    block = _find_block(lines, key)
    if not block:
        return False
    start, end = block
    del lines[start:end]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def merge_telegram_group_config(config_path: Path, chat_id: str) -> bool:
    """Small conservative YAML merge for the exact Hermes Telegram keys we own."""
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("platforms:\n  telegram:\n", encoding="utf-8")
    lines = config_path.read_text(encoding="utf-8").splitlines()
    if _find_block(lines, "platforms") is None:
        lines.append("platforms:")
        lines.append("  telegram:")
    platforms = _find_block(lines, "platforms")
    assert platforms is not None
    p_start, p_end = platforms
    platform_lines = lines[p_start:p_end]
    telegram_rel = _find_block(platform_lines, "telegram", 1, 0)
    if telegram_rel is None:
        lines.insert(p_start + 1, "  telegram:")
        platforms = _find_block(lines, "platforms")
        assert platforms is not None
        p_start, p_end = platforms
        platform_lines = lines[p_start:p_end]
        telegram_rel = _find_block(platform_lines, "telegram", 1, 0)
    assert telegram_rel is not None
    t_start = p_start + telegram_rel[0]
    t_end = p_start + telegram_rel[1]
    block = lines[t_start:t_end]
    changed = False
    if not any(line.strip().startswith("require_mention:") for line in block):
        lines.insert(t_start + 1, "    require_mention: false")
        changed = True
        t_end += 1
        block = lines[t_start:t_end]
    allowed_idx = None
    for i in range(t_start + 1, t_end):
        if lines[i].strip().startswith("group_allowed_chats:"):
            allowed_idx = i
            break
    if allowed_idx is None:
        lines.insert(t_end, "    group_allowed_chats:")
        lines.insert(t_end + 1, f'      - "{chat_id}"')
        changed = True
    else:
        child_end = t_end
        for j in range(allowed_idx + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(lines[j]) - len(lines[j].lstrip(" "))
            if indent <= 4:
                child_end = j
                break
        else:
            child_end = len(lines)
        existing = lines[allowed_idx:child_end]
        if not any(chat_id in line for line in existing):
            lines.insert(child_end, f'      - "{chat_id}"')
            changed = True
    if changed:
        config_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return changed

