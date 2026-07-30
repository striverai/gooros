"""Route Telegram forum topics to Hermes profiles (out-of-tree, update-safe)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
_MAP_PATH = Path(__file__).with_name("topics.json")
_SPACE_RE = re.compile(r"\s+")
_ONBOARDING_LOCK = threading.Lock()
_MEMORY_ENTRY_DELIMITER = "\n\u00a7\n"
_ONBOARDING_MEMORY_MARKER = "telegram onboarding owner profile v1"
_AGENT_LABELS = {
    "orchestrator": "Orchestrator",
    "scout": "Scout",
    "scribe": "Scribe",
    "reach": "Reach",
    "dev": "Dev",
}
_PROMPT7_KEYWORDS = {
    "scout": ("research", "source", "market", "trend", "competitor", "nghien cuu", "nguon", "xu huong", "thi truong", "doi thu", "kiem chung"),
    "scribe": ("write", "draft", "edit", "blog", "article", "caption", "newsletter", "script", "copy", "content", "viet", "bai viet", "noi dung", "bien tap"),
    "reach": ("marketing", "growth", "sales", "lead", "funnel", "campaign", "launch", "monetization", "revenue", "affiliate", "tang truong", "kiem tien", "doanh thu"),
    "dev": ("code", "bug", "api", "dashboard", "automation", "script", "deploy", "repo", "database", "integration", "ky thuat", "tu dong hoa", "tich hop"),
}
_BASIC_QUESTIONS = [
    ("owner_name", "Cau 1/6: Ten cua ban la gi?"),
    ("owner_work", "Cau 2/6: Cong viec, linh vuc kinh doanh, hoac vai tro cua ban la gi?"),
    ("owner_focus", "Cau 3/6: Hien tai ban dang tap trung vao du an hoac muc tieu nao?"),
    ("owner_working_hours", "Cau 4/6: Mui gio va gio lam viec dai khai cua ban nhu the nao?"),
    ("owner_important_people", "Cau 5/6: Nhung nguoi, khach hang, cong su, hoac tai khoan nao la quan trong nhat?"),
    ("owner_cares_about", "Cau 6/6: Dieu gi ban muon Orchestrator luon nam ro, va dieu gi co the giao lai cho doi ngu?"),
]
_CORE_DEEP_FIELDS = [
    "audience_voice",
    "offer_monetization",
    "goals_limits",
    "tools_platforms",
]
_SPECIALIST_DEEP_FIELDS = [
    "scout_context",
    "scribe_context",
    "reach_context",
    "dev_context",
]
_DEEP_FIELD_ORDER = _CORE_DEEP_FIELDS + _SPECIALIST_DEEP_FIELDS + ["delegation_style"]
_DEFAULT_MIN_DEEP_QUESTIONS = 7
_DEFAULT_MAX_DEEP_QUESTIONS = 9
_FIELD_LABELS = {
    "owner_name": "Ten",
    "owner_work": "Cong viec / vai tro",
    "owner_focus": "Trong tam hien tai",
    "owner_working_hours": "Mui gio va gio lam viec",
    "owner_important_people": "Nguoi / tai khoan quan trong",
    "owner_cares_about": "Dieu can nam ro / giao lai",
    "audience_voice": "Khan gia va giong van",
    "offer_monetization": "San pham / de nghi / kiem tien",
    "goals_limits": "Muc tieu va gioi han",
    "tools_platforms": "Cong cu va nen tang",
    "scout_context": "Boi canh cho Scout",
    "scribe_context": "Boi canh cho Scribe",
    "reach_context": "Boi canh cho Reach",
    "dev_context": "Boi canh cho Dev",
    "delegation_style": "Cach Orchestrator nen tu quyet / hoi lai",
}


def _load_map():
    try:
        data = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
        chat_id = str(data.get("chat_id", "")).strip()
        topics = {str(k): str(v) for k, v in (data.get("topics") or {}).items()}
        board_db = str(data.get("board_db", "")).strip()
        onboarding = data.get("onboarding")
        return chat_id, topics, board_db, onboarding if isinstance(onboarding, dict) else None
    except FileNotFoundError:
        return "", {}, "", None
    except Exception as exc:
        logger.warning("telegram_topic_profiles: bad topics.json: %s", exc)
        return "", {}, "", None


def _read(obj, name: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _candidate_objects(event, source):
    yield event
    yield source
    for obj in (event, source):
        for name in ("message", "payload", "data", "update"):
            nested = _read(obj, name)
            if nested is not None and nested is not obj:
                yield nested


def _first_value(event, source, names: tuple[str, ...]):
    for obj in _candidate_objects(event, source):
        if isinstance(obj, str):
            continue
        for name in names:
            value = _read(obj, name)
            if value not in (None, ""):
                return value
    return None


def _message_text(event, source) -> str:
    for obj in _candidate_objects(event, source):
        if isinstance(obj, str) and obj.strip():
            return obj.strip()
        for name in ("text", "message_text", "content", "body", "prompt", "query"):
            value = _read(obj, name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        value = _read(obj, "message")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _summary(text: str, max_chars: int = 150) -> str:
    text = _SPACE_RE.sub(" ", text).strip()
    if not text:
        return "Telegram task"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _board_db_path(configured: str) -> Path:
    raw = configured or os.environ.get("GOOROS_BOARD_DB") or os.environ.get("BOARD_DB") or "~/agent-mission-control/board.db"
    return Path(raw).expanduser()


def _hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME")
    if raw:
        return Path(raw).expanduser()
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path("~/.hermes").expanduser()


def _config_path(config: dict | None, key: str, default: Path) -> Path:
    if isinstance(config, dict):
        raw = str(config.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return default.expanduser()


def _onboarding_state_path(config: dict | None) -> Path:
    return _config_path(config, "state_path", Path(__file__).with_name("onboarding-state.json"))


def _onboarding_memory_path(config: dict | None) -> Path:
    return _config_path(config, "user_memory_path", _hermes_home() / "memories" / "USER.md")


def _owner_profile_path(config: dict | None) -> Path:
    return _config_path(config, "owner_profile_path", _hermes_home() / "owner-profile.json")


def _onboarding_enabled(config: dict | None) -> bool:
    if not isinstance(config, dict):
        return False
    value = config.get("enabled", True)
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on", "*"}


def _allowed_user_tokens() -> set[str]:
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS") or os.environ.get("GATEWAY_ALLOWED_USERS") or ""
    return {part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()}


def _user_allowed_for_onboarding(source) -> bool:
    if _truthy_env("TELEGRAM_ALLOW_ALL_USERS") or _truthy_env("GATEWAY_ALLOW_ALL_USERS"):
        return True
    tokens = _allowed_user_tokens()
    if not tokens:
        return True
    if "*" in tokens:
        return True
    user_id = str(getattr(source, "user_id", "") or "")
    user_name = str(getattr(source, "user_name", "") or "")
    candidates = {user_id, user_name, f"@{user_name}" if user_name else ""}
    return bool(tokens.intersection(candidate for candidate in candidates if candidate))


def _words(text: str) -> list[str]:
    return re.findall(r"[\w@./:+-]+", str(text or "").lower(), flags=re.UNICODE)


def _word_count(text: str) -> int:
    return len(_words(text))


def _answer_value(answers: dict, key: str) -> str:
    value = answers.get(key, "")
    return str(value).strip() if value is not None else ""


def _short_answer(answers: dict, key: str, fallback: str = "phan nay") -> str:
    value = _SPACE_RE.sub(" ", _answer_value(answers, key)).strip()
    if not value:
        return fallback
    return value[:90].rstrip() + ("..." if len(value) > 90 else "")


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    haystack = str(text or "").lower()
    return any(needle in haystack for needle in needles)


def _normalize_prompt7(text: str) -> str:
    try:
        import unicodedata

        decomposed = unicodedata.normalize("NFKD", str(text or ""))
        text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        text = text.replace("Đ", "D").replace("đ", "d")
    except Exception:
        text = str(text or "")
    return _SPACE_RE.sub(" ", text.casefold()).strip()


def _prompt7_handoff_target(current_profile: str, text: str) -> str:
    """Prompt 7 Telegram handoff: reroute obvious out-of-scope specialist tasks."""
    current = str(current_profile or "").strip().lower()
    if current == "orchestrator":
        return current
    if current not in _AGENT_LABELS:
        return current
    normalized = _normalize_prompt7(text)
    if not normalized or normalized.startswith("/"):
        return current
    scores = {}
    for agent, keywords in _PROMPT7_KEYWORDS.items():
        total = 0
        for keyword in keywords:
            if " " in keyword:
                hit = keyword in normalized
            else:
                hit = re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", normalized) is not None
            if hit:
                total += 2 if " " in keyword else 1
        scores[agent] = total
    target, score = max(scores.items(), key=lambda item: (item[1], item[0]))
    if score <= 0 or scores.get(current, 0) >= score:
        return current
    return target


def _prompt7_handoff_notice(target_profile: str) -> str:
    return f"Đây là mảng của {_AGENT_LABELS.get(target_profile, target_profile.title())}, đang chuyển việc này cho họ."


def _send_prompt7_handoff_notice(gateway, event, source, target_profile: str) -> None:
    if gateway is not None:
        _send_onboarding_reply(gateway, event, source, _prompt7_handoff_notice(target_profile))


def _low_information_answer(text: str) -> bool:
    cleaned = str(text or "").strip().lower()
    if _word_count(cleaned) < 4:
        return True
    return cleaned in {"khong biet", "chua biet", "chua ro", "tuy", "sau", "not sure", "unknown"} or _contains_any(
        cleaned,
        ("khong biet", "chua ro", "chua nghi", "not sure", "anything works", "tuy ban"),
    )


def _field_complete(answers: dict, field: str) -> bool:
    return bool(_answer_value(answers, field)) and not _low_information_answer(_answer_value(answers, field))


def _onboarding_limits(config: dict | None) -> tuple[int, int]:
    min_questions = _DEFAULT_MIN_DEEP_QUESTIONS
    max_questions = _DEFAULT_MAX_DEEP_QUESTIONS
    if isinstance(config, dict):
        try:
            min_questions = int(config.get("min_deep_questions", min_questions))
        except (TypeError, ValueError):
            pass
        try:
            max_questions = int(config.get("max_deep_questions", max_questions))
        except (TypeError, ValueError):
            pass
    min_questions = max(4, min(min_questions, len(_DEEP_FIELD_ORDER)))
    max_questions = max(min_questions, min(max_questions, len(_DEEP_FIELD_ORDER)))
    return min_questions, max_questions


def _specialist_priority(answers: dict) -> list[str]:
    text = " ".join(_answer_value(answers, key) for key in (
        "owner_work",
        "owner_focus",
        "owner_important_people",
        "owner_cares_about",
    )).lower()
    scored = []
    signals = {
        "dev_context": ("dev", "tech", "ky thuat", "code", "repo", "dashboard", "automation", "api", "hermes", "gooros", "software", "saas", "system"),
        "reach_context": ("sales", "revenue", "doanh thu", "growth", "marketing", "monet", "khach hang", "client", "lead", "launch", "pilot", "ban"),
        "scribe_context": ("content", "write", "viet", "brand", "voice", "social", "newsletter", "script", "video", "copy"),
        "scout_context": ("research", "nghien cuu", "market", "thi truong", "trend", "competitor", "doi thu", "source", "nguon"),
    }
    for field, terms in signals.items():
        score = sum(1 for term in terms if term in text)
        scored.append((score, field))
    ordered = [field for score, field in sorted(scored, key=lambda item: (-item[0], item[1])) if score > 0]
    for field in ("scout_context", "scribe_context", "reach_context", "dev_context"):
        if field not in ordered:
            ordered.append(field)
    return ordered


def _deep_field_sequence(answers: dict) -> list[str]:
    sequence = list(_CORE_DEEP_FIELDS)
    for field in _specialist_priority(answers):
        if field not in sequence:
            sequence.append(field)
    sequence.append("delegation_style")
    return sequence


def _deep_field_count(record: dict) -> int:
    answers = record.get("answers", {})
    return len([field for field in _DEEP_FIELD_ORDER if _answer_value(answers, field)])


def _build_deep_question(field: str, answers: dict) -> str:
    owner = _short_answer(answers, "owner_name", "ban")
    work = _short_answer(answers, "owner_work", "cong viec cua ban")
    focus = _short_answer(answers, "owner_focus", "trong tam hien tai")
    people = _short_answer(answers, "owner_important_people", "nhung nguoi quan trong")
    cares = _short_answer(answers, "owner_cares_about", "dieu ban quan tam")
    if field == "audience_voice":
        return (
            f"Da co ho so co ban: {owner} dang lam {work} va tap trung vao {focus}. "
            "De ca Scout, Scribe va Reach khong nham doi tuong: nhom khan gia/khach hang uu tien nhat la ai, "
            "ho dang dau o dau, va giong van nao khien ho tin ban?"
        )
    if field == "offer_monetization":
        return (
            f"Voi trong tam {focus}, ban muon Reach/Scribe hieu chinh xac minh dang quang ba, ban, "
            "hoac monetize thu gi? Hay noi ro offer, gia tri khac biet, bang chung tin cay, va neu co thi gia/mo hinh doanh thu."
        )
    if field == "goals_limits":
        return (
            f"Trong khung lam viec { _short_answer(answers, 'owner_working_hours', 'cua ban') }, 30-90 ngay toi ket qua nao la thang loi? "
            "Dong thoi co gioi han nao Orchestrator khong duoc vuot qua: ngan sach, phap ly, thuong hieu, privacy, thoi gian, hay rui ro?"
        )
    if field == "tools_platforms":
        return (
            "De Dev va Orchestrator lam viec that su thay ban, hien ban dang dung nhung cong cu/nen tang nao? "
            "Vi du Telegram, website, CRM, social, email, repo, hosting, analytics, payment, dashboard. Noi them cai nao la source of truth."
        )
    if field == "scout_context":
        return (
            f"Cho Scout: voi {focus}, can theo doi thi truong, doi thu, nguon tin, keyword, hoac xu huong nao? "
            "Nguon nao ban tin, nguon nao nen tranh, va tin hieu nao phai bao gap cho ban?"
        )
    if field == "scribe_context":
        return (
            "Cho Scribe: ban muon tao loai noi dung nao truoc tien, tren kenh nao, va co vi du ve giong van/cau truc nao nen bat chuoc hoac tuyet doi tranh khong?"
        )
    if field == "reach_context":
        return (
            f"Cho Reach: trong nhom {people}, dau hieu nao cho thay mot lead/khach hang dang rat quan trong? "
            "Kenh tang truong nao nen uu tien, KPI nao can theo doi, va cach nao khong duoc dung?"
        )
    if field == "dev_context":
        return (
            "Cho Dev: co he thong, repo, dashboard, automation, API, database, hay quy trinh nao can biet ngay? "
            "Noi ro cai gi duoc tu dong hoa, cai gi can hoi ban truoc khi dung toi."
        )
    if field == "delegation_style":
        return (
            f"Cuoi cung ve cach dieu phoi: voi dieu ban quan tam la {cares}, Orchestrator duoc tu quyet den muc nao, "
            "khi nao bat buoc phai hoi lai ban, va bao cao ket qua nen ngan/dai/chi tiet ra sao?"
        )
    return "Con thong tin quan trong nao neu biet se giup doi ngu Scout, Scribe, Reach va Dev lam viec tot hon cho ban?"


def _clarifying_question(field: str, answers: dict) -> str:
    label = _FIELD_LABELS.get(field, field)
    if field == "audience_voice":
        return "Cho minh them 2-3 vi du cu the ve khach hang/khan gia va giong van ban muon. Mo ta ngan cung du."
    if field == "tools_platforms":
        return "Hay liet ke ten cong cu/nen tang cu the, va noi cai nao la noi luu thong tin dung nhat."
    if field == "goals_limits":
        return "Hay cho minh 1 muc tieu do duoc va 1-2 gioi han khong duoc vuot qua."
    return f"Phan {label} con hoi mong. Cho minh 2-3 chi tiet cu the de Orchestrator co the giao viec dung hon."


def _next_deep_question(record: dict, config: dict | None) -> tuple[str, str] | None:
    answers = record.setdefault("answers", {})
    min_questions, max_questions = _onboarding_limits(config)
    asked = record.setdefault("asked_deep_fields", [])
    answered_count = _deep_field_count(record)
    sequence = _deep_field_sequence(answers)
    required = set(_CORE_DEEP_FIELDS)
    if answered_count >= min_questions and all(_field_complete(answers, field) for field in required):
        optional_incomplete = [field for field in sequence if field not in required and not _field_complete(answers, field) and field not in asked]
        if not optional_incomplete or answered_count >= max_questions:
            return None
    for field in sequence:
        if field in asked:
            continue
        if answered_count >= max_questions and field not in required:
            continue
        if not _field_complete(answers, field):
            asked.append(field)
            record["pending_deep_field"] = field
            return field, _build_deep_question(field, answers)
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_write_text(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
    os.replace(tmp, path)


def _safe_write_json(path: Path, data: object, mode: int | None = None) -> None:
    _safe_write_text(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", mode=mode)


def _load_onboarding_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("schema_version", 1)
            data.setdefault("chats", {})
            if isinstance(data["chats"], dict):
                return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("telegram_topic_profiles: bad onboarding state: %s", exc)
    return {"schema_version": 1, "chats": {}}


def _chat_key(source) -> str:
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", "telegram"))
    chat_id = getattr(source, "chat_id", "") or "unknown-chat"
    user_id = getattr(source, "user_id", "") or "chat"
    return f"{platform}:{chat_id}:{user_id}"


def _new_onboarding_record(source) -> dict:
    return {
        "status": "in_progress",
        "phase": "basic",
        "step": 0,
        "answers": {},
        "pending_deep_field": None,
        "asked_deep_fields": [],
        "clarified_deep_fields": [],
        "started_at": _now(),
        "updated_at": _now(),
        "chat_id": str(getattr(source, "chat_id", "") or ""),
        "user_id": str(getattr(source, "user_id", "") or ""),
        "user_name": str(getattr(source, "user_name", "") or ""),
    }


def _read_memory_entries(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        return []
    if not text:
        return []
    return [entry.strip() for entry in text.split(_MEMORY_ENTRY_DELIMITER) if entry.strip()]


def _upsert_memory_entry(path: Path, marker: str, entry: str) -> None:
    marker_text = f"GOOROS-HERMES-MANAGED: {marker}"
    managed_entry = f"<!-- {marker_text} -->\n{entry.strip()}"
    entries = [existing for existing in _read_memory_entries(path) if marker_text not in existing]
    _safe_write_text(path, _MEMORY_ENTRY_DELIMITER.join([managed_entry, *entries]).rstrip() + "\n", mode=0o600)


def _render_memory(answers: dict) -> str:
    lines = [
        "Gooros Telegram onboarding owner profile.",
        "",
        "Prompt 1 core owner profile:",
    ]
    for key, _question in _BASIC_QUESTIONS:
        lines.append(f"- {_FIELD_LABELS[key]}: {_answer_value(answers, key) or 'unknown'}")
    lines.extend(["", "Prompt 2 adaptive coordination interview:"])
    for key in _DEEP_FIELD_ORDER:
        lines.append(f"- {_FIELD_LABELS[key]}: {_answer_value(answers, key) or 'unknown'}")
    lines.append("")
    lines.append("Use this profile when Orchestrator delegates to Scout, Scribe, Reach, and Dev.")
    return "\n".join(lines)


def _summary_for_owner(answers: dict) -> str:
    lines = [
        "Xong. Minh da luu ho so owner vao tri nho dai han.",
        "",
        "Tom tat ngan:",
    ]
    for key in ("owner_name", "owner_work", "owner_focus", "owner_working_hours", "owner_important_people", "owner_cares_about"):
        lines.append(f"- {_FIELD_LABELS[key]}: {_answer_value(answers, key) or 'unknown'}")
    lines.extend([
        "",
        "Minh cung da luu phan phong van Prompt 2: khan gia/giong van, offer, muc tieu/gioi han, cong cu, va boi canh rieng cho Scout/Scribe/Reach/Dev.",
        "Bay gio ban co the giao viec truc tiep cho Orchestrator.",
    ])
    return "\n".join(lines)


def _write_owner_profile(path: Path, record: dict) -> None:
    _safe_write_json(
        path,
        {
            "schema_version": 1,
            "source": "telegram_onboarding",
            "status": "completed",
            "completed_at": record.get("completed_at"),
            "answers": record.get("answers", {}),
        },
        mode=0o600,
    )


async def _send_reply_async(adapter, chat_id: str, content: str, reply_to: str | None, metadata: dict) -> None:
    try:
        result = adapter.send(chat_id=chat_id, content=content, reply_to=reply_to, metadata=metadata)
        if hasattr(result, "__await__"):
            await result
    except Exception as exc:
        logger.warning("telegram_topic_profiles: onboarding reply failed: %s", exc)


def _adapter_for(gateway, platform):
    adapters = getattr(gateway, "adapters", None) or {}
    if platform in adapters:
        return adapters[platform]
    platform_value = getattr(platform, "value", platform)
    for key, adapter in adapters.items():
        if key == platform_value or getattr(key, "value", key) == platform_value:
            return adapter
    return None


def _send_onboarding_reply(gateway, event, source, content: str) -> bool:
    adapter = _adapter_for(gateway, getattr(source, "platform", None)) if gateway is not None else None
    if adapter is None or not hasattr(adapter, "send"):
        logger.warning("telegram_topic_profiles: no Telegram adapter for onboarding reply")
        return False
    chat_id = str(getattr(source, "chat_id", "") or "")
    if not chat_id:
        return False
    thread_id = getattr(source, "thread_id", None)
    metadata = {"thread_id": str(thread_id)} if thread_id else {}
    reply_to = getattr(event, "message_id", None) or getattr(source, "message_id", None)
    result = _send_reply_async(adapter, chat_id, content, reply_to, metadata)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(result)
    else:
        loop.create_task(result)
    return True


def _basic_question_text(step: int, *, include_intro: bool = False) -> str:
    _field, question = _BASIC_QUESTIONS[step]
    if include_intro:
        return (
            "Minh la Orchestrator. Truoc khi nhan viec, minh hoi nhanh phan ho so owner cot loi. "
            "Sau do minh se phong van tiep thong minh hon de lap doi hinh Scout, Scribe, Reach va Dev. Tra loi ngan gon la du.\n\n"
            f"{question}"
        )
    return question


def _begin_next_deep_question_or_complete(config: dict | None, record: dict) -> str:
    next_question = _next_deep_question(record, config)
    if next_question is None:
        return _complete_onboarding(config, record)
    _field, question = next_question
    if _deep_field_count(record) == 0:
        return (
            "Da co ho so owner cot loi. Bay gio minh se hoi tiep theo chuan Prompt 2: moi cau se dua tren nhung gi ban vua noi, "
            "va minh se dung khi du boi canh de giao viec dung cho Scout, Scribe, Reach va Dev.\n\n"
            f"{question}"
        )
    return question


def _complete_onboarding(config: dict | None, record: dict) -> str:
    record["status"] = "completed"
    record["phase"] = "completed"
    record["pending_deep_field"] = None
    record["completed_at"] = _now()
    record["updated_at"] = record["completed_at"]
    answers = record.get("answers", {})
    _upsert_memory_entry(_onboarding_memory_path(config), _ONBOARDING_MEMORY_MARKER, _render_memory(answers))
    _write_owner_profile(_owner_profile_path(config), record)
    return _summary_for_owner(answers)


def _handle_onboarding(event, source, gateway, config: dict | None):
    if not _onboarding_enabled(config):
        return None
    if not _user_allowed_for_onboarding(source):
        return None
    text = _message_text(event, source)
    if not text:
        return None
    command = text.split(maxsplit=1)[0].lower() if text.startswith("/") else ""
    handled_commands = {"/start", "/onboard", "/onboarding", "/onboarding_status", "/onboarding_reset"}
    state_path = _onboarding_state_path(config)
    key = _chat_key(source)
    with _ONBOARDING_LOCK:
        state = _load_onboarding_state(state_path)
        chats = state.setdefault("chats", {})
        record = chats.get(key)
        completed = isinstance(record, dict) and record.get("status") == "completed"
        if command == "/onboarding_reset":
            record = _new_onboarding_record(source)
            chats[key] = record
            _safe_write_json(state_path, state, mode=0o600)
            reply = _basic_question_text(0, include_intro=True)
            return {"action": "skip", "reason": "gooros-onboarding-reset"} if _send_onboarding_reply(gateway, event, source, reply) else None
        if completed:
            if command in {"/start", "/onboard", "/onboarding", "/onboarding_status"}:
                reply = "Onboarding da hoan tat va da duoc luu vao tri nho dai han. Goi /onboarding_reset neu ban muon lam lai."
                return {"action": "skip", "reason": "gooros-onboarding-complete"} if _send_onboarding_reply(gateway, event, source, reply) else None
            return None
        if command and command not in handled_commands and not isinstance(record, dict):
            return None
        if not isinstance(record, dict):
            record = _new_onboarding_record(source)
            chats[key] = record
            _safe_write_json(state_path, state, mode=0o600)
            reply = _basic_question_text(0, include_intro=True)
            return {"action": "skip", "reason": "gooros-onboarding-start"} if _send_onboarding_reply(gateway, event, source, reply) else None
        if command in {"/start", "/onboard", "/onboarding", "/onboarding_status"}:
            phase = str(record.get("phase") or "basic")
            if phase == "deep":
                pending = record.get("pending_deep_field")
                reply = _build_deep_question(str(pending), record.setdefault("answers", {})) if pending else _begin_next_deep_question_or_complete(config, record)
            else:
                step = int(record.get("step") or 0)
                step = max(0, min(step, len(_BASIC_QUESTIONS) - 1))
                reply = _basic_question_text(step, include_intro=step == 0)
            _safe_write_json(state_path, state, mode=0o600)
            return {"action": "skip", "reason": "gooros-onboarding-resume"} if _send_onboarding_reply(gateway, event, source, reply) else None
        if command:
            reply = "Onboarding dang chay. Tra loi cau hoi hien tai, hoac goi /onboarding_reset de bat dau lai."
            return {"action": "skip", "reason": "gooros-onboarding-command-blocked"} if _send_onboarding_reply(gateway, event, source, reply) else None
        record.setdefault("answers", {})
        record.setdefault("asked_deep_fields", [])
        record.setdefault("clarified_deep_fields", [])
        phase = str(record.get("phase") or "basic")
        if phase == "basic":
            step = int(record.get("step") or 0)
            step = max(0, min(step, len(_BASIC_QUESTIONS)))
            if step >= len(_BASIC_QUESTIONS):
                record["phase"] = "deep"
                reply = _begin_next_deep_question_or_complete(config, record)
            else:
                field, _question = _BASIC_QUESTIONS[step]
                record["answers"][field] = text.strip()
                record["step"] = step + 1
                record["updated_at"] = _now()
                if record["step"] >= len(_BASIC_QUESTIONS):
                    record["phase"] = "deep"
                    reply = _begin_next_deep_question_or_complete(config, record)
                else:
                    reply = _basic_question_text(int(record["step"]))
        else:
            pending = str(record.get("pending_deep_field") or "")
            if pending:
                existing = _answer_value(record["answers"], pending)
                record["answers"][pending] = (existing + "\n" if existing else "") + text.strip()
                record["pending_deep_field"] = None
                record["updated_at"] = _now()
                clarified = record.setdefault("clarified_deep_fields", [])
                if _low_information_answer(record["answers"][pending]) and pending not in clarified:
                    clarified.append(pending)
                    record["pending_deep_field"] = pending
                    reply = _clarifying_question(pending, record["answers"])
                    _safe_write_json(state_path, state, mode=0o600)
                    return {"action": "skip", "reason": "gooros-onboarding-clarify"} if _send_onboarding_reply(gateway, event, source, reply) else None
            reply = _begin_next_deep_question_or_complete(config, record)
        _safe_write_json(state_path, state, mode=0o600)
        return {"action": "skip", "reason": "gooros-onboarding-progress"} if _send_onboarding_reply(gateway, event, source, reply) else None


def _task_id(profile: str, event, source, text: str) -> str:
    chat_id = _first_value(event, source, ("chat_id", "chatId")) or ""
    thread_id = _first_value(event, source, ("thread_id", "threadId", "message_thread_id", "messageThreadId")) or ""
    message_id = _first_value(event, source, ("message_id", "messageId", "update_id", "updateId", "id")) or ""
    if message_id:
        raw = f"{chat_id}:{thread_id}:{message_id}:{profile}"
    else:
        raw = f"{chat_id}:{thread_id}:{profile}:{text[:500]}"
    return "tg-" + hashlib.sha256(str(raw).encode("utf-8", errors="replace")).hexdigest()[:20]


def _record_board_task(profile: str, event, source, configured_board_db: str, *, handoff_from: str = "") -> None:
    text = _message_text(event, source)
    if not text or text.lstrip().startswith("/"):
        return
    board_db = _board_db_path(configured_board_db)
    try:
        board_db.parent.mkdir(parents=True, exist_ok=True)
        task_id = _task_id(profile, event, source, text)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        title = f"{_AGENT_LABELS.get(profile, profile.title())}: {_summary(text)}"
        notes = json.dumps(
            {
                "source": "telegram",
                "agent": profile,
                "prompt7_handoff_from": handoff_from,
                "chat_id": str(_first_value(event, source, ("chat_id", "chatId")) or ""),
                "thread_id": str(_first_value(event, source, ("thread_id", "threadId", "message_thread_id", "messageThreadId")) or ""),
            },
            ensure_ascii=False,
        )
        conn = sqlite3.connect(board_db)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  status TEXT NOT NULL,
                  priority TEXT NOT NULL,
                  notes TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO tasks(id,title,status,priority,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (task_id, title[:180], "in_progress", "medium", notes, now, now),
            )
            conn.execute(
                "UPDATE tasks SET title=?, status='in_progress', priority='medium', notes=?, updated_at=? WHERE id=?",
                (title[:180], notes, now, task_id),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("telegram_topic_profiles: recorded %s task in %s", profile, board_db)
    except Exception as exc:
        logger.warning("telegram_topic_profiles: could not record task in board.db: %s", exc)


def _route(**kwargs):
    event = kwargs.get("event")
    source = getattr(event, "source", None)
    if source is None:
        return None
    pval = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", None))
    if str(pval).lower() != "telegram":
        return None
    map_chat, topics, board_db, onboarding = _load_map()
    chat_id = getattr(source, "chat_id", None)
    if map_chat and chat_id and str(chat_id) != map_chat:
        return None
    handled = _handle_onboarding(event, source, kwargs.get("gateway"), onboarding)
    if handled:
        return handled
    thread_id = getattr(source, "thread_id", None)
    if not thread_id:
        return None
    profile = topics.get(str(thread_id))
    if profile:
        text = _message_text(event, source)
        target_profile = _prompt7_handoff_target(profile, text)
        handoff_from = ""
        if target_profile and target_profile != profile:
            handoff_from = profile
            profile = target_profile
            _send_prompt7_handoff_notice(kwargs.get("gateway"), event, source, profile)
            logger.info("telegram_topic_profiles: Prompt 7 handoff %s -> %s", handoff_from, profile)
        current_profile = getattr(source, "profile", None)
        if profile == "orchestrator":
            if current_profile and current_profile != handoff_from:
                logger.info("telegram_topic_profiles: thread %s -> orchestrator root kept existing profile %s", thread_id, current_profile)
            elif hasattr(source, "profile"):
                source.profile = None
        elif not current_profile or current_profile == handoff_from:
            source.profile = profile
        _record_board_task(profile, event, source, board_db, handoff_from=handoff_from)
        logger.info("telegram_topic_profiles: thread %s -> %s", thread_id, profile)
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _route)
