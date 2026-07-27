from __future__ import annotations

from app.db.models.project import Project, ProjectFile, ProjectStatus
from app.db.models.system import SystemEvent
from app.db.models.user import User


STATUS_EMOJI = {
    ProjectStatus.PENDING_APPROVAL: "🕒",
    ProjectStatus.APPROVED_STOPPED: "✅",
    ProjectStatus.RUNNING: "🟢",
    ProjectStatus.STOPPED: "⏹",
    ProjectStatus.ERROR: "💥",
    ProjectStatus.REJECTED: "❌",
    ProjectStatus.REJECTED_AUTOMATIC: "🚫",
}


def project_status_badge(project: Project) -> str:
    emoji = STATUS_EMOJI.get(project.status, "📦")
    return f"{emoji} <code>{project.status.value}</code>"


def render_project_card(project: Project) -> str:
    return (
        f"<b>{project.name}</b>\n"
        f"• Runtime: <code>{project.runtime.value}</code>\n"
        f"• Status: {project_status_badge(project)}\n"
        f"• Upload: <code>{project.upload_file_name}</code>\n"
        f"• Analysis: <b>{project.analysis_grade.value}</b>\n"
        f"• Entrypoint: <code>{project.entry_file}</code>\n"
        f"• Size: <code>{project.size_bytes}</code> bytes\n"
        f"• Summary: {project.analysis_summary or '-'}"
    )


def render_project_list_item(project: Project) -> str:
    return f"• <b>{project.name}</b> — {project_status_badge(project)}"


def render_file_card(project: Project, project_file: ProjectFile, chunk: str, page: int, total_pages: int) -> str:
    code = chunk if chunk.strip() else "# file is empty"
    return (
        f"🗂 <b>{project_file.relative_path}</b>\n"
        f"• Project: <b>{project.name}</b>\n"
        f"• Size: <code>{project_file.size_bytes}</code> bytes\n"
        f"• Editable: <code>{'yes' if project_file.is_editable else 'no'}</code>\n"
        f"• Page: <code>{page + 1}/{total_pages}</code>\n\n"
        f"<pre>{code[:3000]}</pre>"
    )


def render_diff_preview(relative_path: str, start_line: int, end_line: int, diff_preview: str) -> str:
    preview = diff_preview or "(no visible diff generated)"
    return (
        f"✏️ <b>Patch Preview</b>\n"
        f"• File: <code>{relative_path}</code>\n"
        f"• Range: <code>{start_line}-{end_line}</code>\n\n"
        f"<pre>{preview[:3200]}</pre>"
    )


def render_log_page(project: Project, stream: str, filter_mode: str, page: int, total_pages: int, total_lines: int, text: str) -> str:
    return (
        f"📜 <b>Logs — {project.name}</b>\n"
        f"• Stream: <code>{stream}</code>\n"
        f"• Filter: <code>{filter_mode}</code>\n"
        f"• Page: <code>{page + 1}/{total_pages}</code>\n"
        f"• Matched lines: <code>{total_lines}</code>\n\n"
        f"<pre>{text[:3200]}</pre>"
    )


def render_user_line(user: User) -> str:
    return (
        f"• @{user.username or 'no_username'} — <code>{user.telegram_user_id}</code> "
        f"(<b>{user.status.value}</b>)"
    )


def render_event_line(event: SystemEvent) -> str:
    when = event.created_at.strftime("%m/%d %I:%M %p") if event.created_at else "-"
    return f"• [{when}] <b>{event.event_type}</b> — {event.summary}"
