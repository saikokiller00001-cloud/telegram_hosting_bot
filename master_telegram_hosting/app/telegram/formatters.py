from __future__ import annotations

from html import escape

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
    summary = escape(project.analysis_summary or "No analysis telemetry stored.")
    return (
        "⚡ <b>CYBER TERMINAL :: PROJECT MATRIX</b>\n\n"
        f"<b>{escape(project.name)}</b>\n"
        f"• Runtime Channel: <code>{project.runtime.value}</code>\n"
        f"• Status Core: {project_status_badge(project)}\n"
        f"• Deploy Payload: <code>{escape(project.upload_file_name)}</code>\n"
        f"• Analysis Grade: <b>{escape(project.analysis_grade.value)}</b>\n"
        f"• Entrypoint: <code>{escape(project.entry_file)}</code>\n"
        f"• Storage Footprint: <code>{project.size_bytes}</code> bytes\n"
        f"• Signal: {summary}"
    )


def render_project_list_item(project: Project) -> str:
    return f"• <b>{escape(project.name)}</b> — {project_status_badge(project)}"


def render_file_card(project: Project, project_file: ProjectFile, chunk: str, page: int, total_pages: int) -> str:
    code = escape(chunk if chunk.strip() else "# file is empty")
    return (
        "💎 <b>FILE VAULT</b>\n\n"
        f"• Path: <code>{escape(project_file.relative_path)}</code>\n"
        f"• Project: <b>{escape(project.name)}</b>\n"
        f"• Size: <code>{project_file.size_bytes}</code> bytes\n"
        f"• Editable: <code>{'yes' if project_file.is_editable else 'no'}</code>\n"
        f"• Slice: <code>{page + 1}/{total_pages}</code>\n\n"
        f"<pre>{code[:3000]}</pre>"
    )


def render_diff_preview(relative_path: str, start_line: int, end_line: int, diff_preview: str) -> str:
    preview = escape(diff_preview or "(no visible diff generated)")
    return (
        "🧩 <b>PATCH PREVIEW</b>\n\n"
        f"• File: <code>{escape(relative_path)}</code>\n"
        f"• Line Range: <code>{start_line}-{end_line}</code>\n\n"
        f"<pre>{preview[:3200]}</pre>"
    )


def render_log_page(project: Project, stream: str, filter_mode: str, page: int, total_pages: int, total_lines: int, text: str) -> str:
    return (
        "📜 <b>RUNTIME LOG STREAM</b>\n\n"
        f"• Project: <b>{escape(project.name)}</b>\n"
        f"• Stream: <code>{escape(stream)}</code>\n"
        f"• Filter: <code>{escape(filter_mode)}</code>\n"
        f"• Page: <code>{page + 1}/{total_pages}</code>\n"
        f"• Matched Lines: <code>{total_lines}</code>\n\n"
        f"<pre>{escape(text)[:3200]}</pre>"
    )


def render_user_line(user: User) -> str:
    return (
        f"• @{escape(user.username or 'no_username')} — <code>{user.telegram_user_id}</code> "
        f"(<b>{escape(user.status.value)}</b>)"
    )


def render_event_line(event: SystemEvent) -> str:
    when = event.created_at.strftime("%m/%d %I:%M %p") if event.created_at else "-"
    return f"• [{when}] <b>{escape(event.event_type)}</b> — {escape(event.summary)}"


def render_node_status_card(stats: dict) -> str:
    return (
        "🌐 <b>NODE STATUS :: LIVE TELEMETRY</b>\n\n"
        f"• Host: <code>{escape(str(stats.get('hostname', '-')))}</code>\n"
        f"• Platform: <code>{escape(str(stats.get('platform', '-')))}</code>\n"
        f"• CPU Usage: <b>{stats.get('cpu_percent', 0):.1f}%</b>\n"
        f"• RAM Usage: <b>{stats.get('memory_percent', 0):.1f}%</b> "
        f"(<code>{stats.get('memory_used_mb', 0)}</code>/<code>{stats.get('memory_total_mb', 0)}</code> MB)\n"
        f"• Disk Usage: <b>{stats.get('disk_percent', 0):.1f}%</b> "
        f"(<code>{stats.get('disk_used_gb', 0)}</code>/<code>{stats.get('disk_total_gb', 0)}</code> GB)\n"
        f"• Server Uptime: <code>{escape(str(stats.get('uptime', '-')))}</code>\n"
        f"• Load Avg: <code>{escape(str(stats.get('load_avg', '-')))}</code>\n"
        f"• Stats Engine: <code>{escape(str(stats.get('provider', 'fallback')))}</code>"
    )


def render_billing_card(user: User, overview: dict) -> str:
    return (
        "💳 <b>BILLING & PLAN :: CONTROL CARD</b>\n\n"
        f"• Operator: <b>@{escape(user.username or 'no_username')}</b>\n"
        f"• Tier: <code>{escape(str(overview.get('tier', 'STANDARD')))}</code>\n"
        f"• Projects Used: <code>{overview.get('projects_used', 0)}</code>/<code>{overview.get('project_limit', 0)}</code>\n"
        f"• Stored Payload: <code>{overview.get('storage_used_mb', 0):.2f}</code> MB\n"
        f"• Storage Gate: <code>{overview.get('storage_limit_mb', 0)}</code> MB\n"
        f"• Max Upload Payload: <code>{overview.get('max_upload_mb', 0)}</code> MB\n"
        f"• Runtime Default: <code>{escape(str(overview.get('default_runtime', 'python')))}</code>\n"
        f"• Notifications: <code>{'ON' if overview.get('notifications_enabled') else 'OFF'}</code>"
    )


def render_settings_card(user: User, notifications_enabled: bool, default_runtime: str) -> str:
    return (
        "⚙️ <b>SETTINGS :: USER PROFILE BUS</b>\n\n"
        f"• Operator: <b>@{escape(user.username or 'no_username')}</b>\n"
        f"• Alerts: <code>{'ENABLED' if notifications_enabled else 'DISABLED'}</code>\n"
        f"• Default Runtime: <code>{escape(default_runtime)}</code>\n\n"
        "Use the switches below to mutate your terminal profile."
    )


def render_force_subscribe_card(channel_ref: str) -> str:
    return (
        "🛡️ <b>FORCE SUBSCRIBE GATE</b>\n\n"
        "Access to the command deck is shielded.\n"
        f"Join this channel first: <code>{escape(channel_ref)}</code>\n\n"
        "After joining, tap the recheck switch below."
    )
