from __future__ import annotations

from pathlib import Path
import re


_KEY_RE = re.compile(r"^(\s*)([^:#\s][^:#]*?)\s*:(.*)$")


def _key_line(raw: str) -> tuple[int, str, str] | None:
    match = _KEY_RE.match(raw)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip(), match.group(3)


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    out: list[str] = []
    for char in value:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\" and quote == '"':
            out.append(char)
            escaped = True
            continue
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out).strip()


def _split_inline_items(text: str) -> list[str]:
    parts: list[str] = []
    quote: str | None = None
    escaped = False
    start = 0
    for idx, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        elif char in {"'", '"'}:
            quote = char
        elif char == ",":
            parts.append(text[start:idx].strip())
            start = idx + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _unquote_scalar(value: str) -> str:
    value = _strip_inline_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _quote_yaml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_inline_list(value: str) -> list[str]:
    value = _strip_inline_comment(value)
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote_scalar(item) for item in _split_inline_items(inner)]
    if not value:
        return []
    return [_unquote_scalar(value)]


def _parse_inline_map(value: str) -> list[tuple[str, str]]:
    value = _strip_inline_comment(value)
    if not (value.startswith("{") and value.endswith("}")):
        return []
    inner = value[1:-1].strip()
    if not inner:
        return []
    pairs: list[tuple[str, str]] = []
    for part in _split_inline_items(inner):
        if ":" not in part:
            continue
        key, raw = part.split(":", 1)
        key = _unquote_scalar(key).strip()
        raw = raw.strip()
        if key:
            pairs.append((key, raw))
    return pairs


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


def _ensure_platforms_block(lines: list[str]) -> tuple[int, int, bool]:
    changed = False
    platforms = _find_block(lines, "platforms")
    if platforms is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("platforms:")
        lines.append("  telegram:")
        changed = True
    else:
        p_start, _p_end = platforms
        info = _key_line(lines[p_start])
        value = _strip_inline_comment(info[2]) if info else ""
        if value and value != "{}":
            raise ValueError("unsupported inline platforms config; expand platforms: to a YAML block before merging Telegram group access")
        if value == "{}":
            lines[p_start] = "platforms:"
            changed = True
    platforms = _find_block(lines, "platforms")
    assert platforms is not None
    return platforms[0], platforms[1], changed


def _ensure_telegram_block(lines: list[str], p_start: int, p_end: int) -> tuple[int, int, bool]:
    changed = False
    platform_lines = lines[p_start:p_end]
    telegram_rel = _find_block(platform_lines, "telegram", 1, 0)
    if telegram_rel is None:
        lines.insert(p_start + 1, "  telegram:")
        changed = True
    platforms = _find_block(lines, "platforms")
    assert platforms is not None
    p_start, p_end = platforms
    platform_lines = lines[p_start:p_end]
    telegram_rel = _find_block(platform_lines, "telegram", 1, 0)
    assert telegram_rel is not None
    t_start = p_start + telegram_rel[0]
    t_end = p_start + telegram_rel[1]
    info = _key_line(lines[t_start])
    value = _strip_inline_comment(info[2]) if info else ""
    indent = info[0] if info else 2
    if value:
        if value == "{}":
            lines[t_start] = " " * indent + "telegram:"
            changed = True
        elif value.startswith("{") and value.endswith("}"):
            replacement = [" " * indent + "telegram:"]
            for key, raw in _parse_inline_map(value):
                replacement.append(" " * (indent + 2) + f"{key}: {raw}")
            lines[t_start : t_start + 1] = replacement
            changed = True
        else:
            lines[t_start : t_start + 1] = [
                " " * indent + "telegram:",
                " " * (indent + 2) + f"enabled: {value}",
            ]
            changed = True
    platforms = _find_block(lines, "platforms")
    assert platforms is not None
    p_start, p_end = platforms
    platform_lines = lines[p_start:p_end]
    telegram_rel = _find_block(platform_lines, "telegram", 1, 0)
    assert telegram_rel is not None
    return p_start + telegram_rel[0], p_start + telegram_rel[1], changed


def _direct_child_key(lines: list[str], start: int, end: int, parent_indent: int, key: str) -> int | None:
    child_indent = parent_indent + 2
    for idx in range(start + 1, end):
        info = _key_line(lines[idx])
        if info and info[0] == child_indent and info[1] == key:
            return idx
    return None


def _list_child_end(lines: list[str], key_idx: int, parent_end: int, key_indent: int) -> int:
    for idx in range(key_idx + 1, parent_end):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[idx]) - len(lines[idx].lstrip(" "))
        if indent <= key_indent:
            return idx
    return parent_end


def _list_items_from_block(lines: list[str], key_idx: int, child_end: int) -> list[str]:
    items: list[str] = []
    for line in lines[key_idx + 1 : child_end]:
        stripped = line.strip()
        if stripped.startswith("-"):
            value = stripped[1:].strip()
            if value:
                items.append(_unquote_scalar(value))
    return items


def _replace_or_insert_require_mention(lines: list[str], t_start: int, t_end: int, t_indent: int) -> tuple[int, bool]:
    changed = False
    idx = _direct_child_key(lines, t_start, t_end, t_indent, "require_mention")
    if idx is None:
        token_idx = _direct_child_key(lines, t_start, t_end, t_indent, "token")
        insert_at = token_idx + 1 if token_idx is not None else t_start + 1
        lines.insert(insert_at, " " * (t_indent + 2) + "require_mention: false")
        changed = True
        t_end += 1
    else:
        info = _key_line(lines[idx])
        value = _strip_inline_comment(info[2] if info else "")
        if value.lower() not in {"false", "no", "0", "off"}:
            lines[idx] = " " * (t_indent + 2) + "require_mention: false"
            changed = True
    return t_end, changed


def _replace_or_insert_allowed_chats(lines: list[str], t_start: int, t_end: int, t_indent: int, chat_id: str) -> tuple[int, bool]:
    changed = False
    idx = _direct_child_key(lines, t_start, t_end, t_indent, "group_allowed_chats")
    item_line = " " * (t_indent + 4) + f"- {_quote_yaml_string(chat_id)}"
    if idx is None:
        lines.insert(t_end, " " * (t_indent + 2) + "group_allowed_chats:")
        lines.insert(t_end + 1, item_line)
        return t_end + 2, True
    info = _key_line(lines[idx])
    assert info is not None
    value = _strip_inline_comment(info[2])
    key_indent = info[0]
    if value:
        items = _parse_inline_list(value)
        if chat_id not in items:
            items.append(chat_id)
        replacement = [" " * key_indent + "group_allowed_chats:"]
        replacement.extend(" " * (key_indent + 2) + f"- {_quote_yaml_string(item)}" for item in items)
        lines[idx : idx + 1] = replacement
        return t_end + len(replacement) - 1, True
    child_end = _list_child_end(lines, idx, t_end, key_indent)
    items = _list_items_from_block(lines, idx, child_end)
    if chat_id not in items:
        lines.insert(child_end, item_line)
        changed = True
        t_end += 1
    return t_end, changed


def merge_telegram_group_config(config_path: Path, chat_id: str) -> bool:
    """Conservative YAML merge for Hermes Telegram group-access keys.

    The merge only owns `platforms.telegram.require_mention` and
    `platforms.telegram.group_allowed_chats`. Existing token lines are never
    rewritten in standard block-style configs.
    """
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("platforms:\n  telegram:\n", encoding="utf-8")
    lines = config_path.read_text(encoding="utf-8").splitlines()
    p_start, p_end, changed = _ensure_platforms_block(lines)
    t_start, t_end, telegram_changed = _ensure_telegram_block(lines, p_start, p_end)
    changed = changed or telegram_changed
    info = _key_line(lines[t_start])
    t_indent = info[0] if info else 2
    t_end, rm_changed = _replace_or_insert_require_mention(lines, t_start, t_end, t_indent)
    changed = changed or rm_changed
    t_end, chats_changed = _replace_or_insert_allowed_chats(lines, t_start, t_end, t_indent, chat_id)
    changed = changed or chats_changed
    if changed:
        config_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return changed


def telegram_token_lines(text: str) -> list[str]:
    lines = text.splitlines()
    platforms = _find_block(lines, "platforms")
    if not platforms:
        return []
    p_start, p_end = platforms
    telegram_rel = _find_block(lines[p_start:p_end], "telegram", 1, 0)
    if not telegram_rel:
        return []
    t_start = p_start + telegram_rel[0]
    t_end = p_start + telegram_rel[1]
    tokens: list[str] = []
    for line in lines[t_start + 1 : t_end]:
        info = _key_line(line)
        if info and info[1] == "token":
            tokens.append(line)
    return tokens


def telegram_group_config_values(text: str) -> dict[str, object]:
    lines = text.splitlines()
    platforms = _find_block(lines, "platforms")
    if not platforms:
        return {}
    p_start, p_end = platforms
    telegram_rel = _find_block(lines[p_start:p_end], "telegram", 1, 0)
    if not telegram_rel:
        return {}
    t_start = p_start + telegram_rel[0]
    t_end = p_start + telegram_rel[1]
    info = _key_line(lines[t_start])
    values: dict[str, object] = {}
    if not info:
        return values
    inline_value = _strip_inline_comment(info[2])
    if inline_value.startswith("{") and inline_value.endswith("}"):
        for key, raw in _parse_inline_map(inline_value):
            values[key] = _parse_inline_list(raw) if key == "group_allowed_chats" else _unquote_scalar(raw)
        return values
    t_indent = info[0]
    for idx in range(t_start + 1, t_end):
        child = _key_line(lines[idx])
        if not child or child[0] != t_indent + 2:
            continue
        key = child[1]
        value = _strip_inline_comment(child[2])
        if key == "group_allowed_chats":
            if value:
                values[key] = _parse_inline_list(value)
            else:
                child_end = _list_child_end(lines, idx, t_end, child[0])
                values[key] = _list_items_from_block(lines, idx, child_end)
        else:
            values[key] = _unquote_scalar(value)
    return values


def validate_telegram_group_config_text(text: str, chat_id: str) -> list[str]:
    failures: list[str] = []
    values = telegram_group_config_values(text)
    if not values:
        return ["Hermes config missing platforms.telegram block for Prompt 12"]
    require_mention = str(values.get("require_mention", "")).strip().lower()
    if require_mention not in {"false", "no", "0", "off"}:
        failures.append("platforms.telegram.require_mention is not false")
    allowed = values.get("group_allowed_chats")
    if not isinstance(allowed, list) or chat_id not in {str(item).strip() for item in allowed}:
        failures.append(f'platforms.telegram.group_allowed_chats missing "{chat_id}"')
    return failures
