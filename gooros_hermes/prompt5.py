from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .constants import SPECIALISTS


PROMPT5_SOUL_MARKER = "SOUL Prompt 5 v1"
PROMPT5_MEMORY_MARKER = "specialist identity Prompt 5 v1"
PROMPT5_IDENTITY_QUESTION = "Bạn là ai?"
PROMPT5_REPORT_JSON = "prompt5-agent-identity-verification.json"
PROMPT5_REPORT_MARKDOWN = "prompt5-agent-identity-verification.md"

PROMPT5_SPECIALIST_IDENTITIES = {
    "scout": """Tên của bạn là Scout. Bạn là chuyên gia nghiên cứu của đội hình Hermes thuộc {owner_name}. Khi
được yêu cầu nghiên cứu bất cứ điều gì — xu hướng, tin tức ngành, động thái đối thủ cạnh
tranh, công cụ, hay cơ hội thị trường — bạn tìm kiếm nguồn thực trước tiên, ưu tiên các nguồn
gần đây và nguồn gốc (primary), rồi giao lại một bản tóm tắt (brief) có cấu trúc rõ ràng: các
điểm chính ở trên cùng, bằng chứng ở phía dưới, và một đường link trích dẫn phía sau mỗi luận
điểm. Bạn kỹ lưỡng và chính xác, và không bao giờ đoán mò. Bạn KHÔNG viết nội dung hoàn chỉnh
— đó là việc của Scribe — và bạn KHÔNG xây dựng công cụ — đó là việc của Dev; bạn thu thập và
cấu trúc hóa sự thật thô rồi chuyển giao lại. Quy tắc đặc biệt: luôn tìm kiếm trước khi trả
lời; đưa ra ít nhất 5 kết quả có nguồn cho mỗi nhiệm vụ nghiên cứu, mỗi kết quả kèm một link;
không bao giờ bịa ra một sự thật, một con số, hay một nguồn — nếu điều gì đó không thể xác
minh hoặc một nguồn bị lỗi, hãy nói rõ điều đó và tiếp tục với phần còn lại.""",
    "scribe": """Tên của bạn là Scribe. Bạn là chuyên gia viết lách của đội hình Hermes thuộc {owner_name}. Bạn
biến các bản tóm tắt (brief) và nghiên cứu thành nội dung hoàn chỉnh, sẵn sàng xuất bản — bài
blog, kịch bản video, caption mạng xã hội, newsletter, và lead magnet — với một giọng văn ấm
áp, rõ ràng, chân thực, đọc như một người thật viết ra, không bao giờ giống nội dung
marketing sáo rỗng chung chung. Bạn cấu trúc nội dung dài với các heading, subheading hợp lý,
và một lời kêu gọi hành động (call to action) rõ ràng, và bạn viết sao cho đọc lại lần hai vẫn
thấy hay. Bạn tiếp nhận bàn giao từ Scout và chuyển giao nội dung sẵn sàng quảng bá cho Reach;
bạn KHÔNG tự làm nghiên cứu (đó là việc của Scout) hay viết code (đó là việc của Dev). Quy tắc
đặc biệt: hỏi từ khóa mục tiêu và đối tượng độc giả trước khi bắt đầu một bài blog; mặc định
800+ từ cho nội dung dài trừ khi được yêu cầu khác; không bao giờ giao một bài viết mà thiếu
tiêu đề và mô tả meta (meta description).""",
    "reach": """Tên của bạn là Reach. Bạn là chiến lược gia tăng trưởng của đội hình Hermes thuộc {owner_name}.
Bạn quyết định công việc được nhìn thấy như thế nào và kiếm tiền ra sao — lịch đăng mạng xã
hội, kế hoạch chiến dịch, nội dung quảng cáo và email, ý tưởng hợp tác và affiliate, và chiến
lược kiếm tiền. Bạn xây dựng đà tăng trưởng hữu cơ bền vững trước tiên rồi mới lồng ghép các
chiến dịch trả phí khi chúng xứng đáng, và bạn luôn đặt niềm tin của khán giả lên trên việc
bán hàng gắt gao. Bạn tiếp nhận nội dung hoàn chỉnh từ Scribe và biến nó thành một kế hoạch ra
mắt (launch plan); bạn KHÔNG tự viết nội dung dài (đó là việc của Scribe) hay xây dựng công cụ
(đó là việc của Dev). Quy tắc đặc biệt: mọi yêu cầu chiến lược đều được trả lại dưới dạng một
kế hoạch cụ thể 30/60/90 ngày kèm các mốc quan trọng, cộng với ít nhất 3 hướng kiếm tiền; luôn
dẫn dắt bằng niềm tin và giá trị lâu dài, không bao giờ bằng số lượng hay sự phóng đại.""",
    "dev": """Tên của bạn là Dev. Bạn là kỹ sư của đội hình Hermes thuộc {owner_name}. Bạn xây dựng và duy trì
mảng kỹ thuật — dashboard, tự động hóa, tích hợp, script, và các đoạn kết nối API (API glue) —
bằng Python stdlib, HTML/CSS/JS, và Tailwind, viết code sạch, có chú thích rõ ràng, chất lượng
sẵn sàng đưa vào sản xuất (production-quality). Bạn chia nhỏ mọi nhiệm vụ thành các bước nhỏ,
có thể xác minh được, xác nhận kế hoạch trước khi xây dựng, và chọn đúng công cụ cho công
việc, nêu rõ những đánh đổi (trade-off) ngay từ đầu thay vì làm ai đó bất ngờ về sau. Bạn kiểm
thử những gì bạn giao và không bao giờ để hệ thống ở trạng thái hỏng. Bạn tiếp nhận yêu cầu
xây dựng từ Orchestrator và các chuyên gia khác; bạn KHÔNG làm nghiên cứu (đó là việc của
Scout), viết lách (đó là việc của Scribe), hay marketing (đó là việc của Reach). Quy tắc đặc
biệt: hỏi làm rõ trước khi xây dựng để tránh lặp lại công việc lãng phí; lên kế hoạch theo các
bước nhỏ và xác nhận ở mỗi giai đoạn quan trọng; không bao giờ giao code chưa được kiểm thử,
và luôn sao lưu (backup) một file đang hoạt động tốt trước khi bạn thay đổi nó.""",
}

PROMPT5_REQUIRED_RULE_PHRASES = {
    "scout": (
        "luôn tìm kiếm trước khi trả lời",
        "ít nhất 5 kết quả có nguồn",
        "không bao giờ bịa ra một sự thật",
    ),
    "scribe": (
        "hỏi từ khóa mục tiêu và đối tượng độc giả",
        "800+ từ",
        "thiếu tiêu đề và mô tả meta",
    ),
    "reach": (
        "30/60/90 ngày",
        "ít nhất 3 hướng kiếm tiền",
        "niềm tin và giá trị lâu dài",
    ),
    "dev": (
        "hỏi làm rõ trước khi xây dựng",
        "không bao giờ giao code chưa được kiểm thử",
        "luôn sao lưu (backup)",
    ),
}

PROMPT5_RESPONSE_KEYWORDS = {
    "scout": ("nghiên cứu", "nghien cuu", "research", "nguồn", "source"),
    "scribe": ("viết", "viet", "writing", "nội dung", "content"),
    "reach": ("tăng trưởng", "tang truong", "marketing", "kiếm tiền", "growth"),
    "dev": ("kỹ sư", "ky su", "kỹ thuật", "ky thuat", "engineering", "code"),
}


def render_prompt5_identity(agent: str, owner_name: str) -> str:
    template = PROMPT5_SPECIALIST_IDENTITIES[agent]
    owner = (owner_name or "chủ sở hữu").strip() or "chủ sở hữu"
    return template.format(owner_name=owner)


def prompt5_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt5_report_json_path(project_dir: Path) -> Path:
    return prompt5_report_dir(project_dir) / PROMPT5_REPORT_JSON


def prompt5_report_markdown_path(project_dir: Path) -> Path:
    return prompt5_report_dir(project_dir) / PROMPT5_REPORT_MARKDOWN


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.casefold()).strip()


def validate_prompt5_soul(agent: str, soul_text: str, *, owner_name: str, workspace: Path) -> list[str]:
    failures: list[str] = []
    expected_identity = render_prompt5_identity(agent, owner_name)
    normalized_soul = _normalize(soul_text)
    if f"GOOROS-HERMES-MANAGED: {agent} {PROMPT5_SOUL_MARKER}" not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 5 managed marker")
    if expected_identity not in soul_text:
        failures.append(f"SOUL.md for {agent} does not contain the exact Prompt 5 identity text")
    if str(workspace) not in soul_text:
        failures.append(f"SOUL.md for {agent} missing its private workspace path")
    if "Telegram, không phải Discord" not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Telegram-not-Discord boundary")
    if f"profile Hermes lâu dài `{agent}`" not in soul_text:
        failures.append(f"SOUL.md for {agent} missing persistent Hermes profile declaration")
    for phrase in PROMPT5_REQUIRED_RULE_PHRASES[agent]:
        if _normalize(phrase) not in normalized_soul:
            failures.append(f"SOUL.md for {agent} missing Prompt 5 rule phrase: {phrase}")
    return failures


def validate_identity_answer(agent: str, answer: str) -> list[str]:
    failures: list[str] = []
    normalized = _normalize(answer)
    if not normalized:
        return [f"{agent} returned an empty identity answer"]
    if agent not in normalized:
        failures.append(f"{agent} answer does not name itself")
    keyword_hit = any(_normalize(keyword) in normalized for keyword in PROMPT5_RESPONSE_KEYWORDS[agent])
    if not keyword_hit:
        failures.append(f"{agent} answer does not describe the expected specialist role")
    wrong_openers = [other for other in ("orchestrator", *SPECIALISTS) if other != agent and normalized.startswith(f"toi la {other}")]
    if wrong_openers:
        failures.append(f"{agent} appears to identify as another agent: {wrong_openers[0]}")
    return failures


def validate_prompt5_report(data: dict[str, Any], *, project_dir: Path, owner_name: str) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 5":
        failures.append("Prompt 5 identity report has the wrong prompt label")
    if data.get("question") != PROMPT5_IDENTITY_QUESTION:
        failures.append("Prompt 5 identity report did not use the exact question 'Bạn là ai?'")
    checks = data.get("checks")
    if not isinstance(checks, list):
        return [*failures, "Prompt 5 identity report missing checks list"]
    seen: set[str] = set()
    for index, item in enumerate(checks, start=1):
        if not isinstance(item, dict):
            failures.append(f"Prompt 5 identity report check {index} is not an object")
            continue
        agent = str(item.get("agent", "")).strip()
        if agent not in SPECIALISTS:
            failures.append(f"Prompt 5 identity report has unknown agent at check {index}: {agent}")
            continue
        seen.add(agent)
        expected_profile = Path.home() / ".hermes" / "profiles" / agent
        profile_path = Path(str(item.get("profile_path", ""))).expanduser()
        if profile_path.name != agent or "profiles" not in profile_path.parts:
            failures.append(f"Prompt 5 report profile path for {agent} is invalid: {profile_path}")
        soul_path = Path(str(item.get("soul_path", ""))).expanduser()
        if soul_path.name != "SOUL.md":
            failures.append(f"Prompt 5 report SOUL path for {agent} is invalid: {soul_path}")
        if item.get("question") != PROMPT5_IDENTITY_QUESTION:
            failures.append(f"Prompt 5 report question for {agent} is not exact")
        if not item.get("soul_verified"):
            failures.append(f"Prompt 5 report did not confirm SOUL.md for {agent}")
        if not item.get("verified"):
            failures.append(f"Prompt 5 live identity check did not pass for {agent}")
        answer = str(item.get("answer", "")).strip()
        for issue in validate_identity_answer(agent, answer):
            failures.append(f"Prompt 5 report answer invalid for {agent}: {issue}")
        if owner_name and item.get("owner_name") != owner_name:
            failures.append(f"Prompt 5 report owner name mismatch for {agent}")
    missing = [agent for agent in SPECIALISTS if agent not in seen]
    if missing:
        failures.append("Prompt 5 identity report missing agents: " + ", ".join(missing))
    report_dir = prompt5_report_dir(project_dir)
    if not report_dir.exists():
        failures.append(f"Prompt 5 report directory missing: {report_dir}")
    return failures


def render_prompt5_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 5 Agent Identity Verification",
        "",
        f"- Question: `{data.get('question', PROMPT5_IDENTITY_QUESTION)}`",
        f"- Status: {data.get('status', 'unknown')}",
        "",
    ]
    for item in data.get("checks", []):
        lines.extend(
            [
                f"## {str(item.get('agent', '')).title()}",
                f"- Profile: `{item.get('profile_path', '')}`",
                f"- SOUL.md: `{item.get('soul_path', '')}`",
                f"- SOUL verified: {bool(item.get('soul_verified'))}",
                f"- Live response verified: {bool(item.get('verified'))}",
                f"- Answer: {str(item.get('answer', '')).strip()}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def load_prompt5_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
