from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .constants import AGENTS, SPECIALISTS
from .prompt5 import PROMPT5_IDENTITY_QUESTION, validate_identity_answer as validate_prompt5_identity_answer


PROMPT6_MEMORY_MARKER = "agent memory workspace role boundaries Prompt 6 v1"
PROMPT6_SOUL_MARKER = "Prompt 6 memory workspace boundary continuity v1"
PROMPT6_REPORT_JSON = "prompt6-agent-boundaries-continuity-verification.json"
PROMPT6_REPORT_MARKDOWN = "prompt6-agent-boundaries-continuity-verification.md"

PROMPT6_ROLE_SPECS = {
    "orchestrator": {
        "name": "Orchestrator",
        "role": "điều phối viên chính",
        "personality": "ngắn gọn, có trách nhiệm, kiểm chứng đầu ra và bảo vệ quyền quyết định của owner",
        "memory_scope": "chỉ lưu owner profile, quy tắc vận hành, kế hoạch điều phối, quyết định, blocker và kết quả kiểm chứng liên quan đến toàn đội",
        "identity_keywords": ("orchestrator", "điều phối", "dieu phoi", "coordinator"),
    },
    "scout": {
        "name": "Scout",
        "role": "chuyên gia nghiên cứu",
        "personality": "kỹ lưỡng, chính xác, dựa trên nguồn thật và không đoán mò",
        "memory_scope": "chỉ lưu brief nghiên cứu, nguồn, xu hướng, đối thủ, công cụ, thị trường và insight đã được kiểm chứng",
        "identity_keywords": ("scout", "nghiên cứu", "nghien cuu", "research", "nguồn", "source"),
    },
    "scribe": {
        "name": "Scribe",
        "role": "chuyên gia viết lách và nội dung",
        "personality": "ấm áp, rõ ràng, chân thực, viết như người thật và không sáo rỗng",
        "memory_scope": "chỉ lưu voice, style, brief nội dung, draft, title, meta description, CTA và yêu cầu biên tập",
        "identity_keywords": ("scribe", "viết", "viet", "writing", "nội dung", "content"),
    },
    "reach": {
        "name": "Reach",
        "role": "chiến lược gia marketing, tăng trưởng và kiếm tiền",
        "personality": "thực tế, giữ niềm tin khán giả, ưu tiên giá trị dài hạn và tăng trưởng bền vững",
        "memory_scope": "chỉ lưu chiến dịch, lịch đăng, kênh tăng trưởng, đối tác, affiliate, monetization, KPI và giới hạn marketing",
        "identity_keywords": ("reach", "marketing", "tăng trưởng", "tang truong", "kiếm tiền", "growth"),
    },
    "dev": {
        "name": "Dev",
        "role": "kỹ sư kỹ thuật, tự động hóa và tích hợp",
        "personality": "cẩn thận, có kế hoạch, kiểm thử trước khi bàn giao và không phá trạng thái đang chạy",
        "memory_scope": "chỉ lưu quyết định kỹ thuật, script, API, dashboard, automation, test, bug, deploy và rủi ro hệ thống",
        "identity_keywords": ("dev", "kỹ sư", "ky su", "kỹ thuật", "ky thuat", "engineering", "code"),
    },
}

PROMPT6_BOUNDARY_TESTS = {
    "orchestrator": {
        "request": "Hãy tự nghiên cứu 5 nguồn thị trường và trả brief luôn.",
        "teammate": "Scout",
        "reason": "nghiên cứu và nguồn là phạm vi của Scout",
    },
    "scout": {
        "request": "Hãy viết một bài blog hoàn chỉnh 1200 từ từ brief này.",
        "teammate": "Scribe",
        "reason": "nội dung hoàn chỉnh là phạm vi của Scribe",
    },
    "scribe": {
        "request": "Hãy viết code Python để sửa dashboard.",
        "teammate": "Dev",
        "reason": "code và dashboard là phạm vi của Dev",
    },
    "reach": {
        "request": "Hãy kiểm chứng 5 nguồn nghiên cứu về đối thủ.",
        "teammate": "Scout",
        "reason": "nghiên cứu nguồn là phạm vi của Scout",
    },
    "dev": {
        "request": "Hãy lập kế hoạch marketing 30/60/90 ngày và 3 hướng kiếm tiền.",
        "teammate": "Reach",
        "reason": "marketing và kiếm tiền là phạm vi của Reach",
    },
}

PROMPT6_SCOPE_KEYWORDS = {
    "orchestrator": ("owner", "chủ sở hữu", "chu so huu", "quyết định", "quyet dinh", "điều phối", "dieu phoi", "blocker", "kết quả", "ket qua"),
    "scout": ("nguồn", "nguon", "source", "research", "nghiên cứu", "nghien cuu", "xu hướng", "xu huong", "đối thủ", "doi thu", "thị trường", "thi truong"),
    "scribe": ("draft", "bài viết", "bai viet", "content", "nội dung", "noi dung", "caption", "newsletter", "title", "meta", "cta", "voice"),
    "reach": ("marketing", "growth", "tăng trưởng", "tang truong", "kiếm tiền", "kiem tien", "monetization", "campaign", "affiliate", "kpi", "launch"),
    "dev": ("code", "bug", "test", "deploy", "dashboard", "api", "script", "automation", "tự động hóa", "tu dong hoa", "tích hợp", "tich hop"),
}


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_text = ascii_text.replace("Đ", "D").replace("đ", "d")
    return re.sub(r"\s+", " ", ascii_text.casefold()).strip()


def prompt6_agent_workspace(project_dir: Path, agent: str) -> Path:
    return project_dir / "workspaces" / agent


def prompt6_agent_home(hermes_home: Path, agent: str) -> Path:
    return hermes_home if agent == "orchestrator" else hermes_home / "profiles" / agent


def prompt6_agent_memory_dir(hermes_home: Path, agent: str) -> Path:
    return prompt6_agent_home(hermes_home, agent) / "memories"


def prompt6_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt6_report_json_path(project_dir: Path) -> Path:
    return prompt6_report_dir(project_dir) / PROMPT6_REPORT_JSON


def prompt6_report_markdown_path(project_dir: Path) -> Path:
    return prompt6_report_dir(project_dir) / PROMPT6_REPORT_MARKDOWN


def boundary_teammate(agent: str) -> str:
    return str(PROMPT6_BOUNDARY_TESTS[agent]["teammate"])


def boundary_instruction(agent: str) -> str:
    teammate = boundary_teammate(agent)
    return (
        f"Nếu yêu cầu nằm ngoài phạm vi của bạn, không tự ôm việc; trong đúng MỘT dòng "
        f"nêu teammate phù hợp. Mẫu Prompt 6 tối thiểu: `Đó là phần việc của {teammate}.` "
        "Khi Prompt 7 đã active, hãy chuyển việc thật cho teammate đó thay vì chỉ dừng ở từ chối."
    )


def render_prompt6_policy(agent: str, workspace: Path) -> str:
    spec = PROMPT6_ROLE_SPECS[agent]
    return f"""<!-- GOOROS-HERMES-MANAGED: {agent} {PROMPT6_SOUL_MARKER} -->
## Prompt 6 - bộ nhớ, workspace, ranh giới, continuity

- Danh tính duy nhất: tên `{spec['name']}`, vai trò `{spec['role']}`, tính cách `{spec['personality']}` không thay đổi qua các phiên.
- Bộ nhớ riêng: {spec['memory_scope']}. Không lưu nội dung ngoài phạm vi vào memory của bạn; nếu cần, chuyển sang teammate phù hợp.
- Workspace riêng: `{workspace}`. File, output dài và thao tác local của bạn phải nằm trong workspace hoặc content folder của chính bạn.
- Ranh giới vai trò: {boundary_instruction(agent)}
- Tính liên tục phiên: luôn dùng session hiện có khi runtime cung cấp `--resume`; khi thấy lịch sử trước đó, xây tiếp trên ngữ cảnh cũ thay vì bắt đầu lại.
- Ghi memory có kiểm scope bằng `python3 ~/.hermes/agents/_shared/append-agent-memory.py {agent} "<noi dung can luu>"`.
<!-- END GOOROS-HERMES-MANAGED: {agent} {PROMPT6_SOUL_MARKER} -->"""


def render_prompt6_memory(agent: str, workspace: Path) -> str:
    spec = PROMPT6_ROLE_SPECS[agent]
    return (
        f"Prompt 6 policy for {agent}: stable identity name={spec['name']}; role={spec['role']}; "
        f"personality={spec['personality']}; private memory scope={spec['memory_scope']}; "
        f"private workspace={workspace}; role boundary={boundary_instruction(agent)}; "
        "session continuity requires resuming the latest available session and building on prior context."
    )


def is_memory_relevant(agent: str, text: str) -> bool:
    normalized = normalize(text)
    shared_keywords = ("owner", "chu so huu", "chủ sở hữu", "gooros", "hermes", "telegram", "workspace", "session", "prompt")
    return any(normalize(keyword) in normalized for keyword in (*shared_keywords, *PROMPT6_SCOPE_KEYWORDS[agent]))


def validate_prompt6_identity_answer(agent: str, answer: str) -> list[str]:
    if agent in SPECIALISTS:
        return validate_prompt5_identity_answer(agent, answer)
    failures: list[str] = []
    normalized = normalize(answer)
    if not normalized:
        return [f"{agent} returned an empty identity answer"]
    if "orchestrator" not in normalized:
        failures.append("orchestrator answer does not name itself")
    if not any(normalize(keyword) in normalized for keyword in PROMPT6_ROLE_SPECS["orchestrator"]["identity_keywords"]):
        failures.append("orchestrator answer does not describe the coordination role")
    return failures


def validate_boundary_answer(agent: str, answer: str) -> list[str]:
    failures: list[str] = []
    stripped = answer.strip()
    teammate = boundary_teammate(agent)
    normalized = normalize(stripped)
    if not stripped:
        return [f"{agent} returned an empty boundary answer"]
    if "\n" in stripped or "\r" in stripped:
        failures.append(f"{agent} boundary answer is not one line")
    if len(stripped) > 240:
        failures.append(f"{agent} boundary answer is too long for a one-line refusal")
    if normalize(teammate) not in normalized:
        failures.append(f"{agent} boundary answer does not name {teammate}")
    if "phan viec" not in normalized and "viec cua" not in normalized and "phu hop" not in normalized:
        failures.append(f"{agent} boundary answer does not clearly refuse by role boundary")
    return failures


def validate_prompt6_soul(agent: str, soul_text: str, workspace: Path) -> list[str]:
    failures: list[str] = []
    normalized = normalize(soul_text)
    spec = PROMPT6_ROLE_SPECS[agent]
    if f"GOOROS-HERMES-MANAGED: {agent} {PROMPT6_SOUL_MARKER}" not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 6 managed policy")
    for phrase in (
        "Danh tính duy nhất",
        "Bộ nhớ riêng",
        "Workspace riêng",
        "Ranh giới vai trò",
        "Tính liên tục phiên",
        "append-agent-memory.py",
    ):
        if normalize(phrase) not in normalized:
            failures.append(f"SOUL.md for {agent} missing Prompt 6 phrase: {phrase}")
    if str(workspace) not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 6 workspace path")
    if normalize(str(spec["role"])) not in normalized:
        failures.append(f"SOUL.md for {agent} missing stable role")
    if normalize(boundary_teammate(agent)) not in normalized:
        failures.append(f"SOUL.md for {agent} missing role-boundary teammate")
    return failures


def validate_prompt6_report(data: dict[str, Any], *, project_dir: Path) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 6":
        failures.append("Prompt 6 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 6 report status is not passed")
    identity_checks = data.get("identity_checks")
    boundary_checks = data.get("boundary_checks")
    workspace_checks = data.get("workspace_checks")
    if not isinstance(identity_checks, list):
        failures.append("Prompt 6 report missing identity_checks list")
        identity_checks = []
    if not isinstance(boundary_checks, list):
        failures.append("Prompt 6 report missing boundary_checks list")
        boundary_checks = []
    if not isinstance(workspace_checks, list):
        failures.append("Prompt 6 report missing workspace_checks list")
        workspace_checks = []
    for section, checks in (("identity", identity_checks), ("boundary", boundary_checks), ("workspace", workspace_checks)):
        seen = {str(item.get("agent", "")).strip() for item in checks if isinstance(item, dict)}
        missing = [agent for agent in AGENTS if agent not in seen]
        if missing:
            failures.append(f"Prompt 6 report missing {section} checks for: " + ", ".join(missing))
    for item in identity_checks:
        if not isinstance(item, dict) or item.get("agent") not in AGENTS:
            continue
        agent = item["agent"]
        if not item.get("verified"):
            failures.append(f"Prompt 6 identity check did not pass for {agent}")
        for issue in validate_prompt6_identity_answer(agent, str(item.get("answer", ""))):
            failures.append(f"Prompt 6 identity answer invalid for {agent}: {issue}")
    for item in boundary_checks:
        if not isinstance(item, dict) or item.get("agent") not in AGENTS:
            continue
        agent = item["agent"]
        if not item.get("verified"):
            failures.append(f"Prompt 6 boundary check did not pass for {agent}")
        for issue in validate_boundary_answer(agent, str(item.get("answer", ""))):
            failures.append(f"Prompt 6 boundary answer invalid for {agent}: {issue}")
    for item in workspace_checks:
        if not isinstance(item, dict) or item.get("agent") not in AGENTS:
            continue
        agent = item["agent"]
        expected = prompt6_agent_workspace(project_dir, agent)
        if Path(str(item.get("workspace_path", ""))).expanduser() != expected:
            failures.append(f"Prompt 6 workspace path mismatch for {agent}")
        if not item.get("workspace_verified"):
            failures.append(f"Prompt 6 workspace check did not pass for {agent}")
    return failures


def render_prompt6_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 6 Agent Boundary And Continuity Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Question: `{PROMPT5_IDENTITY_QUESTION}`",
        "",
        "## Identity Answers",
        "",
    ]
    for item in data.get("identity_checks", []):
        lines.append(f"- {item.get('agent')}: {str(item.get('answer', '')).strip()}")
    lines.extend(["", "## Boundary Checks", ""])
    for item in data.get("boundary_checks", []):
        lines.append(f"- {item.get('agent')} -> {item.get('expected_teammate')}: {str(item.get('answer', '')).strip()}")
    lines.extend(["", "## Workspaces", ""])
    for item in data.get("workspace_checks", []):
        lines.append(f"- {item.get('agent')}: `{item.get('workspace_path')}`")
    return "\n".join(lines).rstrip() + "\n"


def load_prompt6_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
