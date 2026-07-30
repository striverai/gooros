from __future__ import annotations

from pathlib import Path


PROMPT29_MEMORY_MARKER = "document archiving workflow Prompt 29 v1"
PROMPT29_SOUL_MARKER = "Prompt 29 long document archiving workflow v1"


def prompt29_content_dir(project_dir: Path, agent: str) -> Path:
    return project_dir / "content" / agent


def render_prompt29_policy(agent: str, project_dir: Path) -> str:
    content_dir = prompt29_content_dir(project_dir, agent)
    return f"""<!-- GOOROS-HERMES-MANAGED: {agent} {PROMPT29_SOUL_MARKER} -->
## Prompt 29 - Quy trinh luu tru tai lieu dai

Khi ban tao bat ky tai lieu dai nao hon khoang 15 dong - bai viet, bao cao nghien cuu, kich ban, dan y, brief, ke hoach, transcript, tom tat, tai lieu chien luoc, outreach draft, huong dan ky thuat, post-mortem, hoac deliverable de doc lai/tai su dung - ban BAT BUOC luu no vao thu muc rieng cua chinh ban, khong do inline vao chat.

Thu muc cua ban: `{content_dir}`

Quy tac co dinh:
1. Chi luu vao thu muc cua chinh ban; khong bao gio ghi vao thu muc agent khac.
2. Chi dung Markdown `.md`; khong dung `.txt`, khong dump noi dung dai vao chat.
3. Ten file: `YYYY-MM-DD_short-kebab-case-title.md`, viet thuong, dung dau gach noi, khong khoang trang hoac ky tu dac biet.
4. Dong dau tien phai la heading cap cao nhat `# Title` de tab Content lay lam tieu de.
5. Noi dung phai la markdown that: `##`, `###`, **bold**, `inline code`, fenced code blocks, va danh sach `-`.
6. Noi dung dai phai luu thanh file: bai viet, research summary, script, outreach draft, strategic doc, meeting note, technical guide, post-mortem.
7. Noi dung ngan thi tra loi truc tiep trong chat: xac nhan, status update, cau tra loi nhanh, ket qua tool ngan.
8. Moi file chi chua mot tai lieu; neu mot nhiem vu co nhieu deliverable, tao nhieu file rieng.
9. Khong am tham ghi de; neu trung ten file, them `-v2`, `-v3`, hoac dung title cu the hon.
10. O dung pham vi vai tro; neu ngoai vai tro, chuyen giao dung teammate.
11. Sau khi luu, xac nhan mot dong: `{agent} -> <full path> - <tom tat mot dong>`.

Tab Content doc truc tiep tu `~/agent-mission-control/content/`; tai lieu dai trong chat se khong xuat hien de xem truoc, sua, tai xuong, hoac tai su dung.
<!-- END GOOROS-HERMES-MANAGED: {agent} {PROMPT29_SOUL_MARKER} -->"""


def render_prompt29_memory(agent: str, project_dir: Path) -> str:
    return (
        f"Prompt 29 document archiving rule for {agent}: any reusable/deliverable output longer than about 15 lines "
        f"must be saved as Markdown in {prompt29_content_dir(project_dir, agent)}. Use filename "
        "YYYY-MM-DD_short-kebab-case-title.md, first line # Title, one document per file, never write to another "
        "agent's folder, avoid silent overwrite by adding -v2/-v3, stay in role or hand off, and confirm in one "
        f"line: {agent} -> <full path> - <one-line summary>."
    )


def validate_prompt29_soul(agent: str, soul_text: str) -> list[str]:
    marker = f"GOOROS-HERMES-MANAGED: {agent} {PROMPT29_SOUL_MARKER}"
    lower = soul_text.lower()
    required = (
        marker,
        f"content/{agent}",
        "YYYY-MM-DD_short-kebab-case-title.md",
        "# Title",
    )
    failures = [f"Prompt 29 SOUL missing: {token}" for token in required if token not in soul_text]
    for token in ("15 dong", "khong am tham ghi de", "sau khi luu"):
        if token not in lower:
            failures.append(f"Prompt 29 SOUL missing: {token}")
    return failures
