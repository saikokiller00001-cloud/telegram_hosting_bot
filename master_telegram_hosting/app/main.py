from __future__ import annotations

import asyncio
import logging
import re
from html import escape
from math import ceil
from pathlib import Path

import redis.asyncio as redis
from sqlalchemy import func, select
from telethon.errors import MessageNotModifiedError
from telethon import TelegramClient, events

from app.config import get_settings
from app.db.base import Base, engine, session_scope
from app.db.models.project import Project, ProjectStatus
from app.db.models.user import User, UserStatus
from app.runtime.process_supervisor import ProcessSupervisor
from app.runtime.reconciliation import RuntimeReconciler
from app.runtime.systemd_launcher import SystemdLauncher
from app.services.admin_service import AdminService
from app.services.analysis_service import AnalysisService
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.file_manager_service import FileManagerService
from app.services.logs_service import LogsService
from app.services.module_manager_service import ModuleManagerService
from app.services.notification_service import NotificationService
from app.services.project_runtime_service import ProjectRuntimeService
from app.services.upload_service import UploadService
from app.telegram.buttons import (
    activity_markup,
    admin_dashboard_markup,
    admin_projects_markup,
    admin_user_detail_markup,
    admin_users_markup,
    approval_markup,
    file_browser_markup,
    file_view_markup,
    logs_view_markup,
    main_menu_markup,
    patch_confirm_markup,
    project_actions_markup,
    projects_list_markup,
    user_projects_markup,
)
from app.telegram.formatters import (
    render_diff_preview,
    render_event_line,
    render_file_card,
    render_log_page,
    render_project_card,
    render_project_list_item,
    render_user_line,
)
from app.telegram.state_store import StateStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def safe_event_edit(event, *args, **kwargs):
    try:
        return await event.edit(*args, **kwargs)
    except MessageNotModifiedError:
        return None


def parse_package_input(raw_text: str) -> list[str]:
    packages: list[str] = []
    seen: set[str] = set()

    for chunk in re.split(r"[\n,]+", raw_text):
        package_name = chunk.strip()
        if not package_name or package_name in seen:
            continue
        packages.append(package_name)
        seen.add(package_name)

    return packages


def render_module_install_result(project: Project, runtime: str, result: dict) -> str:
    runtime_label = "pip" if runtime == "python" else "npm"
    installed = result.get("installed", [])
    failed = result.get("failed", [])

    lines = [
        "⚡ <b>Module Control Report</b>",
        "",
        f"Target project: <b>{escape(project.name)}</b>",
        f"Runtime channel: <code>{runtime_label}</code>",
        f"Queue processed: <code>{len(installed) + len(failed)}</code>",
        f"Status: <code>{'OK' if result.get('success') else 'PARTIAL / FAILED'}</code>",
        "",
    ]

    if installed:
        lines.append("✅ <b>Installed</b>")
        lines.extend(f"• <code>{escape(pkg)}</code>" for pkg in installed)
        lines.append("")

    if failed:
        lines.append("❌ <b>Failed</b>")
        for item in failed:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                pkg, reason = item[0], item[1]
            else:
                pkg, reason = str(item), "Unknown error"
            lines.append(f"• <code>{escape(str(pkg))}</code> — {escape(str(reason))}")
        lines.append("")

    message = result.get("message")
    if message:
        lines.append(f"└─ {escape(str(message))}")

    lines.append("")
    lines.append("Ready for the next command.")
    return "\n".join(lines)


settings = get_settings()
client = TelegramClient(settings.app_name, settings.api_id, settings.api_hash)
redis_client = redis.from_url(settings.redis_url, decode_responses=True)
state_store = StateStore(redis_client, settings)

analysis_service = AnalysisService(settings)
upload_service = UploadService(settings, analysis_service)
approval_service = ApprovalService()
audit_service = AuditService()
logs_service = LogsService(settings)
file_manager_service = FileManagerService(settings)
admin_service = AdminService(settings)
systemd_launcher = SystemdLauncher(settings)
process_supervisor = ProcessSupervisor(settings, systemd_launcher)
notification_service: NotificationService | None = None
runtime_service: ProjectRuntimeService | None = None
runtime_reconciler: RuntimeReconciler | None = None


async def upsert_user(event) -> tuple[User, bool]:
    sender = await event.get_sender()
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == sender.id))
        is_new = False
        if not user:
            is_new = True
            user = User(
                telegram_user_id=sender.id,
                username=getattr(sender, "username", None),
                first_name=getattr(sender, "first_name", None),
                last_name=getattr(sender, "last_name", None),
                is_admin=sender.id == settings.owner_telegram_id,
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            await session.flush()
        else:
            user.username = getattr(sender, "username", None)
            user.first_name = getattr(sender, "first_name", None)
            user.last_name = getattr(sender, "last_name", None)
        return user, is_new


async def get_owned_or_admin_project(session, actor: User, project_id: int) -> Project:
    project = await session.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise ValueError("Project not found.")
    if actor.is_admin or project.owner_user_id == actor.id:
        return project
    raise ValueError("You do not have access to this project.")


async def send_home(event, user: User, *, edit: bool = False) -> None:
    text = (
        "✨ <b>Telegram Hosting Platform</b>\n\n"
        "Upload, approve, run, inspect logs, patch files, and control hosted projects fully inside Telegram."
    )
    if edit:
        await safe_event_edit(event, text, parse_mode="html", buttons=main_menu_markup(user.is_admin))
    else:
        await event.respond(text, parse_mode="html", buttons=main_menu_markup(user.is_admin))


@client.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    global notification_service
    user, is_new = await upsert_user(event)
    if notification_service and is_new:
        await notification_service.notify_new_user(user)
    await send_home(event, user)


@client.on(events.CallbackQuery)
async def callback_router(event):
    global notification_service, runtime_service
    data = event.data.decode("utf-8")
    user, _ = await upsert_user(event)

    if user.status != UserStatus.ACTIVE:
        await event.answer("Your account is not allowed to use this bot.", alert=True)
        return

    if data == "menu:home":
        await send_home(event, user, edit=True)
        return

    if data == "menu:upload":
        await state_store.set_upload_wait(user.telegram_user_id)
        await safe_event_edit(event, 
            "📤 Send a `.py`, `.js`, or `.zip` file now.\n\n"
            "The bot will run syntax checks and security heuristics immediately, then send it for approval.",
            buttons=main_menu_markup(user.is_admin),
        )
        return

    if data.startswith("menu:projects:"):
        page = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            projects = list(
                await session.scalars(
                    select(Project)
                    .where(Project.owner_user_id == user.id, Project.is_deleted.is_(False))
                    .order_by(Project.created_at.desc())
                )
            )
        total_pages = max(1, ceil(len(projects) / settings.page_size_projects))
        page = max(0, min(page, total_pages - 1))
        start = page * settings.page_size_projects
        page_projects = projects[start : start + settings.page_size_projects]
        if not page_projects:
            await safe_event_edit(event, "You have no projects yet.", buttons=main_menu_markup(user.is_admin))
            return
        lines = ["📁 <b>My Projects</b>", ""]
        for project in page_projects:
            lines.append(render_project_list_item(project))
        await safe_event_edit(event, 
            "\n".join(lines),
            parse_mode="html",
            buttons=projects_list_markup([project.id for project in page_projects], page, total_pages),
        )
        return

    if data.startswith("project:view:"):
        project_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
        await safe_event_edit(event, 
            render_project_card(project),
            parse_mode="html",
            buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
        )
        return

    if data.startswith("project:modules:"):
        project_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
            runtime = project.runtime.value if hasattr(project.runtime, "value") else str(project.runtime)

        await state_store.set_packages_wait(user.telegram_user_id, project_id=project.id, runtime=runtime)

        runtime_label = "pip" if runtime == "python" else "npm"
        example_block = (
            "<pre>requests==2.32.3\naiofiles</pre>"
            if runtime == "python"
            else "<pre>express\naxios</pre>"
        )

        await safe_event_edit(
            event,
            "⚡ <b>Module Control</b>\n\n"
            f"Terminal ready for <b>{escape(project.name)}</b>.\n"
            f"Runtime channel: <code>{runtime_label}</code>\n\n"
            "Enter package names separated by comma or new line.\n"
            "Paste the queue now:\n"
            f"{example_block}",
            parse_mode="html",
            buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
        )
        return

    if data.startswith("project:analysis:"):
        project_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
        issues = (project.analysis_report_json or {}).get("issues", [])[:15]
        lines = [f"🧪 <b>Analysis — {project.name}</b>", f"Summary: {project.analysis_summary or '-'}", ""]
        if not issues:
            lines.append("No warnings were detected in the current stored analysis.")
        else:
            for issue in issues:
                lines.append(
                    f"• [{issue.get('level')}] {issue.get('file')}:{issue.get('line') or '-'} — {issue.get('message')}"
                )
        await safe_event_edit(event, 
            "\n".join(lines),
            parse_mode="html",
            buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
        )
        return

    if data.startswith("project:start:"):
        project_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await runtime_service.start_project(session, db_user, project_id)
            await safe_event_edit(event, 
                f"▶️ Started <b>{project.name}</b>.\n\n{render_project_card(project)}",
                parse_mode="html",
                buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
            )
        except Exception as exc:
            await event.answer(str(exc), alert=True)
        return

    if data.startswith("project:stop:"):
        project_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await runtime_service.stop_project(session, db_user, project_id)
            await safe_event_edit(event, 
                f"⏹ Stop requested for <b>{project.name}</b>.\n\n{render_project_card(project)}",
                parse_mode="html",
                buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
            )
        except Exception as exc:
            await event.answer(str(exc), alert=True)
        return

    if data.startswith("project:restart:"):
        project_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await runtime_service.restart_project(session, db_user, project_id)
            await safe_event_edit(event, 
                f"🔄 Restarted <b>{project.name}</b>.\n\n{render_project_card(project)}",
                parse_mode="html",
                buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
            )
        except Exception as exc:
            await event.answer(str(exc), alert=True)
        return

    if data.startswith("project:files:"):
        _, _, project_id_raw, page_raw = data.split(":")
        project_id = int(project_id_raw)
        page = int(page_raw)
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
            files, total_pages = await file_manager_service.list_files_paginated(session, project.id, page)
        lines = [f"🗂 <b>Files — {project.name}</b>", ""]
        for item in files:
            lines.append(
                f"• <code>{item.relative_path}</code> — {'editable' if item.is_editable else 'binary/view only'}"
            )
        await safe_event_edit(event, 
            "\n".join(lines),
            parse_mode="html",
            buttons=file_browser_markup([item.id for item in files], project.id, page, total_pages),
        )
        return

    if data.startswith("file:view:"):
        _, _, file_id_raw, page_raw = data.split(":")
        file_id = int(file_id_raw)
        page = int(page_raw)
        async with session_scope() as session:
            project, project_file, chunk, current_page, total_pages = await file_manager_service.read_text_file_chunk(
                session, file_id, page
            )
            await get_owned_or_admin_project(session, user, project.id)
        await safe_event_edit(event, 
            render_file_card(project, project_file, chunk, current_page, total_pages),
            parse_mode="html",
            buttons=file_view_markup(file_id, project.id, current_page, total_pages, project_file.is_editable),
        )
        return

    if data.startswith("file:edit:"):
        file_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project_file = await file_manager_service.get_file(session, file_id)
            if not project_file:
                await event.answer("File not found.", alert=True)
                return
            project = await get_owned_or_admin_project(session, user, project_file.project_id)
        if not project_file.is_editable:
            await event.answer("This file is not editable in the MVP editor.", alert=True)
            return
        await state_store.set_editor_replace_wait(
            user.telegram_user_id,
            project_id=project.id,
            relative_path=project_file.relative_path,
            file_id=file_id,
        )
        await safe_event_edit(event, 
            "✏️ <b>Replace mode enabled</b>\n\n"
            f"Send the full replacement content for:\n<code>{project_file.relative_path}</code>\n\n"
            f"Limit: {settings.max_editable_text_file_kb} KB.",
            parse_mode="html",
            buttons=file_view_markup(file_id, project.id, 0, 1, True),
        )
        return

    if data.startswith("file:patch:") and data not in {"file:patch_confirm", "file:patch_cancel"}:
        file_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project_file = await file_manager_service.get_file(session, file_id)
            if not project_file:
                await event.answer("File not found.", alert=True)
                return
            project = await get_owned_or_admin_project(session, user, project_file.project_id)
        if not project_file.is_editable:
            await event.answer("This file is not editable in patch mode.", alert=True)
            return
        await state_store.set_editor_patch_wait(
            user.telegram_user_id,
            project_id=project.id,
            relative_path=project_file.relative_path,
            file_id=file_id,
        )
        await safe_event_edit(event, 
            "🧩 <b>Patch mode enabled</b>\n\n"
            "Send your patch in this format:\n"
            "<pre>12-18\n\nnew replacement lines here</pre>\n"
            "The first line is the line range. The remaining text replaces that range.",
            parse_mode="html",
            buttons=file_view_markup(file_id, project.id, 0, 1, True),
        )
        return

    if data == "file:patch_confirm":
        state = await state_store.get_state(user.telegram_user_id)
        if not state or state.get("kind") != "awaiting_patch_confirm":
            await event.answer("No patch preview is waiting for confirmation.", alert=True)
            return
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await file_manager_service.apply_line_patch(
                    session,
                    project_id=int(state["project_id"]),
                    relative_path=state["relative_path"],
                    start_line=int(state["start_line"]),
                    end_line=int(state["end_line"]),
                    replacement_text=state["replacement_text"],
                    editor_user_id=db_user.id,
                )
                await audit_service.record(
                    session,
                    event_type="FILE_PATCH_APPLIED",
                    summary=f"Applied patch to {state['relative_path']} in {project.name}",
                    severity="info",
                    actor_user_id=db_user.id,
                    target_user_id=project.owner_user_id,
                    project_id=project.id,
                    payload={
                        "relative_path": state["relative_path"],
                        "start_line": state["start_line"],
                        "end_line": state["end_line"],
                    },
                )
            await state_store.clear(user.telegram_user_id)
            await safe_event_edit(event, 
                "✅ Patch applied successfully.\n\n"
                f"Project status is now <code>{project.status.value}</code>.",
                parse_mode="html",
                buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
            )
        except Exception as exc:
            await state_store.clear(user.telegram_user_id)
            await event.answer(str(exc), alert=True)
        return

    if data == "file:patch_cancel":
        await state_store.clear(user.telegram_user_id)
        await safe_event_edit(event, "❌ Patch canceled.", buttons=main_menu_markup(user.is_admin))
        return

    if data.startswith("logs:view:"):
        _, _, project_id_raw, stream, filter_mode, page_raw = data.split(":")
        project_id = int(project_id_raw)
        page = int(page_raw)
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
        text, current_page, total_pages, total_lines = logs_service.read_log_page(
            project,
            stream=stream,
            page=page,
            filter_mode=filter_mode,
        )
        await safe_event_edit(event, 
            render_log_page(project, stream, filter_mode, current_page, total_pages, total_lines, text),
            parse_mode="html",
            buttons=logs_view_markup(project.id, stream, filter_mode, current_page, total_pages),
        )
        return

    if data == "admin:dashboard":
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        async with session_scope() as session:
            stats = await admin_service.dashboard_stats(session)
        text = (
            "⚙️ <b>Admin Dashboard</b>\n\n"
            f"• Users: <b>{stats['total_users']}</b>\n"
            f"• Active users: <b>{stats['active_users']}</b>\n"
            f"• Banned users: <b>{stats['banned_users']}</b>\n"
            f"• Projects: <b>{stats['total_projects']}</b>\n"
            f"• Pending approvals: <b>{stats['pending_approvals']}</b>\n"
            f"• Running projects: <b>{stats['running_projects']}</b>\n"
            f"• Stopped projects: <b>{stats['stopped_projects']}</b>\n"
            f"• Error state projects: <b>{stats['errored_projects']}</b>\n"
            f"• Activity events: <b>{stats['total_events']}</b>"
        )
        await safe_event_edit(event, text, parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data.startswith("admin:approvals:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        page = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            pending_projects, total_pages = await admin_service.list_pending_projects(session, page)
        if not pending_projects:
            await safe_event_edit(event, "No pending approvals right now.", buttons=admin_dashboard_markup())
            return
        project = pending_projects[0]
        text = (
            f"🕒 <b>Approval Queue</b>\n"
            f"• Item: <code>{page + 1}/{total_pages}</code>\n\n"
            f"{render_project_card(project)}"
        )
        await safe_event_edit(event, text, parse_mode="html", buttons=approval_markup(project.id, page, total_pages))
        return

    if data.startswith("approval:approve:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        _, _, project_id_raw, queue_raw = data.split(":")
        project_id = int(project_id_raw)
        queue_index = int(queue_raw)
        async with session_scope() as session:
            project = await approval_service.approve_project(session, project_id, user.id)
            target_user = await session.scalar(select(User).where(User.id == project.owner_user_id))
            await audit_service.record(
                session,
                event_type="PROJECT_APPROVED",
                summary=f"Approved project {project.name}",
                severity="info",
                actor_user_id=user.id,
                target_user_id=project.owner_user_id,
                project_id=project.id,
                payload={"queue_index": queue_index},
            )
        if notification_service and target_user:
            await notification_service.notify_project_decision(target_user.telegram_user_id, project)
        await safe_event_edit(event, 
            f"✅ Approved <b>{project.name}</b>.\n\nReturning to queue...",
            parse_mode="html",
            buttons=admin_dashboard_markup(),
        )
        return

    if data.startswith("approval:reject:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        _, _, project_id_raw, queue_raw = data.split(":")
        project_id = int(project_id_raw)
        queue_index = int(queue_raw)
        async with session_scope() as session:
            project = await approval_service.reject_project(session, project_id, user.id)
            target_user = await session.scalar(select(User).where(User.id == project.owner_user_id))
            await audit_service.record(
                session,
                event_type="PROJECT_REJECTED",
                summary=f"Rejected project {project.name}",
                severity="warning",
                actor_user_id=user.id,
                target_user_id=project.owner_user_id,
                project_id=project.id,
                payload={"queue_index": queue_index},
            )
        if notification_service and target_user:
            await notification_service.notify_project_decision(target_user.telegram_user_id, project)
        await safe_event_edit(event, 
            f"❌ Rejected <b>{project.name}</b>.\n\nReturning to queue...",
            parse_mode="html",
            buttons=admin_dashboard_markup(),
        )
        return

    if data.startswith("approval:analysis:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        _, _, project_id_raw, queue_raw = data.split(":")
        project_id = int(project_id_raw)
        queue_index = int(queue_raw)
        async with session_scope() as session:
            project = await session.scalar(select(Project).where(Project.id == project_id))
            _, total_pages = await admin_service.list_pending_projects(session, queue_index)
        if not project:
            await event.answer("Project not found.", alert=True)
            return
        issues = (project.analysis_report_json or {}).get("issues", [])[:15]
        lines = [f"🔍 <b>Approval Analysis — {project.name}</b>", f"Summary: {project.analysis_summary or '-'}", ""]
        if not issues:
            lines.append("No warnings in stored analysis.")
        else:
            for issue in issues:
                lines.append(
                    f"• [{issue.get('level')}] {issue.get('file')}:{issue.get('line') or '-'} — {issue.get('message')}"
                )
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=approval_markup(project.id, queue_index, total_pages))
        return

    if data.startswith("admin:users:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        page = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            users, total_pages = await admin_service.list_recent_users(session, page)
        lines = ["👥 <b>Recent Users</b>", ""]
        for item in users:
            lines.append(render_user_line(item))
        await safe_event_edit(event, 
            "\n".join(lines),
            parse_mode="html",
            buttons=admin_users_markup([item.telegram_user_id for item in users], page, total_pages),
        )
        return

    if data.startswith("admin:user:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        telegram_user_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            target = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
            project_count = await session.scalar(select(func.count(Project.id)).where(Project.owner_user_id == target.id)) if target else 0
        if not target:
            await event.answer("User not found.", alert=True)
            return
        text = (
            "👤 <b>User Detail</b>\n\n"
            f"• Username: @{target.username or 'no_username'}\n"
            f"• Telegram ID: <code>{target.telegram_user_id}</code>\n"
            f"• Name: {target.first_name or '-'} {target.last_name or ''}\n"
            f"• Status: <b>{target.status.value}</b>\n"
            f"• Admin: <code>{'yes' if target.is_admin else 'no'}</code>\n"
            f"• Projects: <b>{project_count}</b>"
        )
        await safe_event_edit(event, 
            text,
            parse_mode="html",
            buttons=admin_user_detail_markup(target.telegram_user_id, target.status == UserStatus.BANNED),
        )
        return

    if data.startswith("admin:user_projects:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        _, _, telegram_user_id_raw, page_raw = data.split(":")
        telegram_user_id = int(telegram_user_id_raw)
        page = int(page_raw)
        async with session_scope() as session:
            target_user, projects, total_pages = await admin_service.list_user_projects(session, telegram_user_id, page)
        if not target_user:
            await event.answer("User not found.", alert=True)
            return
        lines = [f"📁 <b>Projects of @{target_user.username or 'no_username'}</b>", ""]
        for project in projects:
            lines.append(render_project_list_item(project))
        await safe_event_edit(event, 
            "\n".join(lines),
            parse_mode="html",
            buttons=user_projects_markup([project.id for project in projects], telegram_user_id, page, total_pages),
        )
        return

    if data.startswith("admin:block_user:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        telegram_user_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            target = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
            if not target:
                await event.answer("User not found.", alert=True)
                return
            target.status = UserStatus.BANNED
            target.banned_reason = "Blocked from in-bot admin panel."
            await audit_service.record(
                session,
                event_type="USER_BANNED",
                summary=f"Banned user {telegram_user_id}",
                severity="warning",
                actor_user_id=user.id,
                target_user_id=target.id,
                payload={"telegram_user_id": telegram_user_id},
            )
        await safe_event_edit(event, 
            f"🚫 Blocked user <code>{telegram_user_id}</code>.",
            parse_mode="html",
            buttons=admin_dashboard_markup(),
        )
        return

    if data.startswith("admin:unban_user:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        telegram_user_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            target = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
            if not target:
                await event.answer("User not found.", alert=True)
                return
            target.status = UserStatus.ACTIVE
            target.banned_reason = None
            await audit_service.record(
                session,
                event_type="USER_UNBANNED",
                summary=f"Unbanned user {telegram_user_id}",
                severity="info",
                actor_user_id=user.id,
                target_user_id=target.id,
                payload={"telegram_user_id": telegram_user_id},
            )
        await safe_event_edit(event, 
            f"♻️ Unbanned user <code>{telegram_user_id}</code>.",
            parse_mode="html",
            buttons=admin_dashboard_markup(),
        )
        return

    if data.startswith("admin:projects_filter:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        _, _, status_filter, page_raw = data.split(":")
        page = int(page_raw)
        async with session_scope() as session:
            projects, total_pages = await admin_service.list_projects(session, status_filter=status_filter, page=page)
        lines = [f"📦 <b>Projects — filter: {status_filter}</b>", ""]
        for item in projects:
            lines.append(render_project_list_item(item))
        await safe_event_edit(event, 
            "\n".join(lines),
            parse_mode="html",
            buttons=admin_projects_markup([item.id for item in projects], status_filter, page, total_pages),
        )
        return

    if data.startswith("admin:project:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        project_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project = await session.scalar(select(Project).where(Project.id == project_id))
        if not project:
            await event.answer("Project not found.", alert=True)
            return
        await safe_event_edit(event, 
            render_project_card(project),
            parse_mode="html",
            buttons=project_actions_markup(project.id, project.status.value, is_admin=True),
        )
        return

    if data == "admin:stats":
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        async with session_scope() as session:
            stats = await admin_service.dashboard_stats(session)
        text = (
            "🖥 <b>System Stats</b>\n\n"
            f"• Users total/active/banned: <b>{stats['total_users']}</b> / <b>{stats['active_users']}</b> / <b>{stats['banned_users']}</b>\n"
            f"• Projects total/running/stopped/error: <b>{stats['total_projects']}</b> / <b>{stats['running_projects']}</b> / <b>{stats['stopped_projects']}</b> / <b>{stats['errored_projects']}</b>\n"
            f"• Pending approvals: <b>{stats['pending_approvals']}</b>\n"
            f"• Activity events: <b>{stats['total_events']}</b>"
        )
        await safe_event_edit(event, text, parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data.startswith("admin:activity:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        page = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            events_list, total_pages = await admin_service.list_events(session, page)
        lines = ["🧾 <b>Recent Activity</b>", ""]
        for item in events_list:
            lines.append(render_event_line(item))
        await safe_event_edit(event, 
            "\n".join(lines),
            parse_mode="html",
            buttons=activity_markup(page, total_pages),
        )
        return

    await event.answer("That action is not implemented yet.", alert=True)


@client.on(events.NewMessage)
async def message_router(event):
    global notification_service
    if event.raw_text and event.raw_text.startswith("/"):
        return

    user, _ = await upsert_user(event)
    if user.status != UserStatus.ACTIVE:
        return

    state = await state_store.get_state(user.telegram_user_id)
    if not state:
        return

    if state["kind"] == "awaiting_packages":
        if not event.raw_text:
            await event.reply("Send package names as plain text, separated by comma or new line.")
            return

        packages = parse_package_input(event.raw_text)
        if not packages:
            await event.reply("No package names detected. Send comma or newline separated package names.")
            return

        progress_message = await event.reply(
            "⏳ <b>Installing modules...</b>\n\n"
            "<code>Resolving package queue and validating package names...</code>",
            parse_mode="html",
        )

        project = None
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.telegram_user_id == user.telegram_user_id))
                project = await get_owned_or_admin_project(session, db_user, int(state["project_id"]))
                runtime = state.get("runtime") or (project.runtime.value if hasattr(project.runtime, "value") else str(project.runtime))

                module_manager = ModuleManagerService(session)
                if runtime == "nodejs":
                    result = await module_manager.install_npm_packages(project.id, packages)
                    event_type = "NPM_PACKAGES_INSTALLED"
                    summary = f"Requested npm install for {len(packages)} package(s) in {project.name}"
                else:
                    result = await module_manager.install_pip_packages(project.id, packages)
                    event_type = "PIP_PACKAGES_INSTALLED"
                    summary = f"Requested pip install for {len(packages)} package(s) in {project.name}"

                await audit_service.record(
                    session,
                    event_type=event_type,
                    summary=summary,
                    severity="info" if result.get("success") else "warning",
                    actor_user_id=db_user.id,
                    target_user_id=project.owner_user_id,
                    project_id=project.id,
                    payload={
                        "runtime": runtime,
                        "packages": packages,
                        "installed": result.get("installed", []),
                        "failed": result.get("failed", []),
                        "message": result.get("message"),
                    },
                )

            await state_store.clear(user.telegram_user_id)
            await progress_message.edit(
                render_module_install_result(project, runtime, result),
                parse_mode="html",
                buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin),
            )
        except Exception as exc:
            logger.exception("Package installation failed")
            await state_store.clear(user.telegram_user_id)
            buttons = (
                project_actions_markup(project.id, project.status.value, is_admin=user.is_admin)
                if project
                else main_menu_markup(user.is_admin)
            )
            await progress_message.edit(
                "❌ <b>Module Control Failure</b>\n\n"
                "<code>Install session aborted.</code>\n"
                f"Reason: <code>{escape(str(exc))}</code>\n\n"
                "Retry from the project panel when ready.",
                parse_mode="html",
                buttons=buttons,
            )
        return

    if state["kind"] == "awaiting_upload":
        if not event.file:
            await event.reply("Please send a file upload, not plain text.")
            return

        temp_dir = settings.temp_root / str(user.telegram_user_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        downloaded = await event.download_media(file=str(temp_dir))
        if not downloaded:
            await event.reply("Failed to download the uploaded file.")
            return

        path = Path(downloaded)
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.telegram_user_id == user.telegram_user_id))
                project, analysis = await upload_service.create_project_from_upload(session, db_user, path, path.name)
                await audit_service.record(
                    session,
                    event_type="PROJECT_UPLOADED",
                    summary=f"Uploaded project {project.name}",
                    severity="info",
                    actor_user_id=db_user.id,
                    target_user_id=db_user.id,
                    project_id=project.id,
                    payload={"analysis_grade": analysis.grade.value},
                )
            await state_store.clear(user.telegram_user_id)
            text = (
                f"📦 <b>{project.name}</b> uploaded successfully.\n"
                f"• Status: <code>{project.status.value}</code>\n"
                f"• Analysis: <b>{analysis.grade.value}</b>\n"
                f"• Summary: {analysis.summary}"
            )
            await event.reply(text, parse_mode="html", buttons=main_menu_markup(user.is_admin))
            if notification_service:
                await notification_service.notify_new_upload(user, project)
        except Exception as exc:
            logger.exception("Upload processing failed")
            await state_store.clear(user.telegram_user_id)
            await event.reply(f"❌ Upload failed: <code>{str(exc)}</code>", parse_mode="html")
        return

    if state["kind"] == "awaiting_editor_replace":
        if not event.raw_text:
            await event.reply("Send plain text content for the replacement.")
            return
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.telegram_user_id == user.telegram_user_id))
                project = await get_owned_or_admin_project(session, db_user, int(state["project_id"]))
                updated = await file_manager_service.overwrite_text_file(
                    session,
                    project.id,
                    state["relative_path"],
                    event.raw_text,
                    db_user.id,
                )
                await audit_service.record(
                    session,
                    event_type="FILE_REPLACED",
                    summary=f"Replaced file {state['relative_path']} in {updated.name}",
                    severity="info",
                    actor_user_id=db_user.id,
                    target_user_id=updated.owner_user_id,
                    project_id=updated.id,
                    payload={"relative_path": state["relative_path"]},
                )
            await state_store.clear(user.telegram_user_id)
            await event.reply(
                "✅ File content replaced successfully.\n\n"
                f"Project status is now <code>{updated.status.value}</code>."
                + (" Re-approval is required before running again." if updated.status == ProjectStatus.PENDING_APPROVAL else ""),
                parse_mode="html",
                buttons=main_menu_markup(user.is_admin),
            )
        except Exception as exc:
            logger.exception("Editor replace failed")
            await state_store.clear(user.telegram_user_id)
            await event.reply(f"❌ Edit failed: <code>{str(exc)}</code>", parse_mode="html")
        return

    if state["kind"] == "awaiting_editor_patch":
        if not event.raw_text:
            await event.reply("Send text in the patch format: first line is `start-end`, then the replacement block.")
            return
        lines = event.raw_text.splitlines()
        if not lines:
            await event.reply("Patch input was empty.")
            return
        match = re.match(r"^(\d+)\s*-\s*(\d+)$", lines[0].strip())
        if not match:
            await event.reply("First line must be a range like `12-18`.")
            return
        start_line = int(match.group(1))
        end_line = int(match.group(2))
        replacement_text = "\n".join(lines[1:]).lstrip("\n")
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.telegram_user_id == user.telegram_user_id))
                project = await get_owned_or_admin_project(session, db_user, int(state["project_id"]))
                project_obj, project_file, _new_content, diff_preview = await file_manager_service.build_patch_preview(
                    session,
                    file_id=int(state["file_id"]),
                    start_line=start_line,
                    end_line=end_line,
                    replacement_text=replacement_text,
                )
            await state_store.set_patch_preview(
                user.telegram_user_id,
                project_id=project.id,
                relative_path=state["relative_path"],
                file_id=int(state["file_id"]),
                start_line=start_line,
                end_line=end_line,
                replacement_text=replacement_text,
                diff_preview=diff_preview,
            )
            await event.reply(
                render_diff_preview(project_file.relative_path, start_line, end_line, diff_preview),
                parse_mode="html",
                buttons=patch_confirm_markup(int(state["file_id"]), project.id),
            )
        except Exception as exc:
            logger.exception("Patch preview failed")
            await state_store.clear(user.telegram_user_id)
            await event.reply(f"❌ Patch preview failed: <code>{str(exc)}</code>", parse_mode="html")
        return


async def bootstrap() -> None:
    global notification_service, runtime_service, runtime_reconciler
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await client.start(bot_token=settings.bot_token)
    notification_service = NotificationService(client, settings)
    runtime_service = ProjectRuntimeService(
        settings,
        process_supervisor,
        notification_service,
        logs_service,
        audit_service,
    )
    runtime_reconciler = RuntimeReconciler(
        settings,
        process_supervisor,
        runtime_service,
        audit_service,
        logs_service,
    )
    reconcile_summary = await runtime_reconciler.reconcile()
    logger.info("Runtime reconciliation summary: %s", reconcile_summary)
    logger.info("Bot started")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(bootstrap())
