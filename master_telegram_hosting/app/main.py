from __future__ import annotations

import asyncio
import logging
import re
from html import escape
from math import ceil
from pathlib import Path

import redis.asyncio as redis
from sqlalchemy import func, select
from telethon import TelegramClient, events
from telethon.errors import MessageNotModifiedError

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
    file_delete_confirm_markup,
    file_view_markup,
    force_subscribe_gate_markup,
    logs_view_markup,
    main_menu_markup,
    patch_confirm_markup,
    project_actions_markup,
    project_destroy_confirm_markup,
    projects_list_markup,
    settings_markup,
    user_projects_markup,
)
from app.telegram.formatters import (
    render_billing_card,
    render_diff_preview,
    render_event_line,
    render_file_card,
    render_force_subscribe_card,
    render_log_page,
    render_node_status_card,
    render_project_card,
    render_project_list_item,
    render_settings_card,
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
        "⚡ <b>TERMINAL :: MODULE INSTALL REPORT</b>",
        "",
        f"• Project: <b>{escape(project.name)}</b>",
        f"• Channel: <code>{runtime_label}</code>",
        f"• Queue Size: <code>{len(installed) + len(failed)}</code>",
        f"• Result: <code>{'SUCCESS' if result.get('success') else 'PARTIAL / FAILED'}</code>",
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
    if result.get("message"):
        lines.append(f"└─ {escape(str(result['message']))}")
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


async def get_user_default_runtime(user: User) -> str:
    async with session_scope() as session:
        db_user = await session.scalar(select(User).where(User.id == user.id))
        return await admin_service.get_default_runtime(session, db_user.id)


async def send_home(event, user: User, *, edit: bool = False) -> None:
    default_runtime = await get_user_default_runtime(user)
    text = (
        "⚡ <b>CYBER TERMINAL :: COMMAND DECK</b>\n\n"
        "Launch deployments, inspect live nodes, mutate packages, and control every hosted runtime from one encrypted console.\n\n"
        f"• Default runtime profile: <code>{escape(default_runtime)}</code>"
    )
    if edit:
        await safe_event_edit(event, text, parse_mode="html", buttons=main_menu_markup(user.is_admin))
    else:
        await event.respond(text, parse_mode="html", buttons=main_menu_markup(user.is_admin))


async def send_force_subscribe_gate(event, channel_ref: str, *, edit: bool = False) -> None:
    text = render_force_subscribe_card(channel_ref)
    if edit:
        await safe_event_edit(event, text, parse_mode="html", buttons=force_subscribe_gate_markup())
    else:
        await event.respond(text, parse_mode="html", buttons=force_subscribe_gate_markup())


async def is_force_subscribed(user: User) -> tuple[bool, str | None]:
    if user.is_admin:
        return True, None
    async with session_scope() as session:
        db_user = await session.scalar(select(User).where(User.id == user.id))
        channel_ref = await admin_service.get_force_subscribe_channel(session)
    if not channel_ref:
        return True, None
    try:
        permissions = await client.get_permissions(channel_ref, user.telegram_user_id)
        if permissions is None:
            return False, channel_ref
        return True, channel_ref
    except Exception:
        return False, channel_ref


async def ensure_force_subscribed(event, user: User, *, edit: bool = False) -> bool:
    allowed, channel_ref = await is_force_subscribed(user)
    if allowed:
        return True
    await send_force_subscribe_gate(event, channel_ref or "@channel", edit=edit)
    return False


async def animate_installation(progress_message, project_name: str) -> None:
    frames = [
        f"⚡ <b>TERMINAL ACTIVE</b>\n\n<code>[sys] Locking target project: {escape(project_name)}</code>",
        "⚡ <b>TERMINAL ACTIVE</b>\n\n<code>[sys] Resolving dependencies...</code>",
        "⚡ <b>TERMINAL ACTIVE</b>\n\n<code>[sys] Fetching packets...</code>",
        "⚡ <b>TERMINAL ACTIVE</b>\n\n<code>[sys] Building environment...</code>",
        "⚡ <b>TERMINAL ACTIVE</b>\n\n<code>[sys] Finalizing runtime hooks...</code>",
    ]
    for frame in frames:
        await progress_message.edit(frame, parse_mode="html")
        await asyncio.sleep(0.7)


@client.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    global notification_service
    user, is_new = await upsert_user(event)
    if notification_service and is_new:
        await notification_service.notify_new_user(user)
    if not await ensure_force_subscribed(event, user, edit=False):
        return
    await send_home(event, user)


@client.on(events.CallbackQuery)
async def callback_router(event):
    global notification_service, runtime_service
    data = event.data.decode("utf-8")
    user, _ = await upsert_user(event)

    if user.status != UserStatus.ACTIVE:
        await event.answer("🕵️‍♂️ ACCESS DENIED: your user key is disabled.", alert=True)
        return

    if data == "fsub:refresh":
        if await ensure_force_subscribed(event, user, edit=True):
            await send_home(event, user, edit=True)
        return

    if data.startswith("admin:fsub:") and not user.is_admin:
        await event.answer("🛡️ Admin only.", alert=True)
        return

    if not await ensure_force_subscribed(event, user, edit=True):
        return

    if data == "menu:home":
        await send_home(event, user, edit=True)
        return

    if data == "menu:upload":
        default_runtime = await get_user_default_runtime(user)
        await state_store.set_upload_wait(user.telegram_user_id)
        await safe_event_edit(
            event,
            "🚀 <b>DEPLOY NODE :: UPLOAD GATE OPEN</b>\n\n"
            "Send a <code>.py</code>, <code>.js</code>, or <code>.zip</code> payload now.\n"
            "Static analysis, security heuristics, and approval routing will fire automatically.\n\n"
            f"• Profile runtime hint: <code>{escape(default_runtime)}</code>",
            parse_mode="html",
            buttons=main_menu_markup(user.is_admin),
        )
        return

    if data == "menu:node_status":
        await safe_event_edit(event, render_node_status_card(admin_service.node_status()), parse_mode="html", buttons=main_menu_markup(user.is_admin))
        return

    if data == "menu:billing":
        async with session_scope() as session:
            db_user = await session.scalar(select(User).where(User.id == user.id))
            overview = await admin_service.get_billing_overview(session, db_user)
        await safe_event_edit(event, render_billing_card(user, overview), parse_mode="html", buttons=main_menu_markup(user.is_admin))
        return

    if data == "menu:settings":
        async with session_scope() as session:
            db_user = await session.scalar(select(User).where(User.id == user.id))
            notifications_enabled = await admin_service.notifications_enabled(session, db_user.id)
            default_runtime = await admin_service.get_default_runtime(session, db_user.id)
        await safe_event_edit(event, render_settings_card(user, notifications_enabled, default_runtime), parse_mode="html", buttons=settings_markup(notifications_enabled, default_runtime, user.is_admin))
        return

    if data == "settings:toggle_notifications":
        async with session_scope() as session:
            db_user = await session.scalar(select(User).where(User.id == user.id))
            enabled = await admin_service.toggle_notifications(session, db_user.id)
            default_runtime = await admin_service.get_default_runtime(session, db_user.id)
        await safe_event_edit(event, render_settings_card(user, enabled, default_runtime), parse_mode="html", buttons=settings_markup(enabled, default_runtime, user.is_admin))
        return

    if data.startswith("settings:set_runtime:"):
        runtime = data.rsplit(":", 1)[-1]
        async with session_scope() as session:
            db_user = await session.scalar(select(User).where(User.id == user.id))
            await admin_service.set_default_runtime(session, db_user.id, runtime, updated_by_user_id=db_user.id)
            enabled = await admin_service.notifications_enabled(session, db_user.id)
        await safe_event_edit(event, render_settings_card(user, enabled, runtime), parse_mode="html", buttons=settings_markup(enabled, runtime, user.is_admin))
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
            await safe_event_edit(event, "🕵️‍♂️ <b>EMPTY GRID</b>\n\nNo servers are wired to your account yet.", parse_mode="html", buttons=main_menu_markup(user.is_admin))
            return
        lines = ["🗂️ <b>MY SERVER MATRIX</b>", ""]
        for project in page_projects:
            lines.append(render_project_list_item(project))
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=projects_list_markup([project.id for project in page_projects], page, total_pages))
        return

    if data.startswith("project:view:"):
        project_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
        await safe_event_edit(event, render_project_card(project), parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        return

    if data.startswith("project:modules:"):
        project_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
            runtime = project.runtime.value if hasattr(project.runtime, "value") else str(project.runtime)
        await state_store.set_state(user.telegram_user_id, {"kind": "awaiting_packages", "project_id": project.id, "runtime": runtime})
        runtime_label = "pip" if runtime == "python" else "npm"
        example_block = "<pre>requests==2.32.3\naiofiles</pre>" if runtime == "python" else "<pre>express\naxios</pre>"
        await safe_event_edit(
            event,
            "⚡ <b>MODULE CONTROL :: TERMINAL READY</b>\n\n"
            f"Project lock acquired for <b>{escape(project.name)}</b>.\n"
            f"Runtime channel: <code>{runtime_label}</code>\n\n"
            "Enter package names separated by comma or newline.\n"
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
        lines = [f"🧪 <b>ANALYSIS GRID — {escape(project.name)}</b>", f"Signal: {escape(project.analysis_summary or '-')}", ""]
        if not issues:
            lines.append("✅ No warnings were detected in the stored analysis snapshot.")
        else:
            for issue in issues:
                lines.append(
                    f"• [{escape(str(issue.get('level')))}] {escape(str(issue.get('file')))}:{escape(str(issue.get('line') or '-'))} — {escape(str(issue.get('message')))}"
                )
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        return

    if data.startswith("project:start:"):
        project_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await runtime_service.start_project(session, db_user, project_id)
            await safe_event_edit(event, f"✅ <b>DEPLOYMENT SUCCESSFUL</b>\n\n{render_project_card(project)}", parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        except Exception as exc:
            await event.answer(str(exc), alert=True)
        return

    if data.startswith("project:stop:"):
        project_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await runtime_service.stop_project(session, db_user, project_id)
            await safe_event_edit(event, f"⏹ <b>RUNTIME HALTED</b>\n\n{render_project_card(project)}", parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        except Exception as exc:
            await event.answer(str(exc), alert=True)
        return

    if data.startswith("project:restart:"):
        project_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await runtime_service.restart_project(session, db_user, project_id)
            await safe_event_edit(event, f"🔄 <b>STACK REBOOT COMPLETE</b>\n\n{render_project_card(project)}", parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        except Exception as exc:
            await event.answer(str(exc), alert=True)
        return

    if data.startswith("project:destroy:") and not data.startswith("project:destroy_confirm:"):
        project_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
        await safe_event_edit(
            event,
            "🗑️ <b>DESTRUCTION PROTOCOL ARMED</b>\n\n"
            f"Project <b>{escape(project.name)}</b> will be wiped from disk, logs, versions, and database indexes.\n\n"
            "This operation cannot be rolled back.",
            parse_mode="html",
            buttons=project_destroy_confirm_markup(project.id, user.is_admin),
        )
        return

    if data.startswith("project:destroy_confirm:"):
        project_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project = await get_owned_or_admin_project(session, db_user, project_id)
                if project.status == ProjectStatus.RUNNING:
                    await runtime_service.stop_project(session, db_user, project_id, notify_user=False)
                destroyed = await file_manager_service.destroy_project(session, project_id)
                await audit_service.record(
                    session,
                    event_type="PROJECT_DESTROYED",
                    summary=f"Destroyed project {destroyed['name']}",
                    severity="warning",
                    actor_user_id=db_user.id,
                    target_user_id=destroyed['owner_user_id'],
                    project_id=None,
                    payload=destroyed,
                )
            await safe_event_edit(event, "🗑️ <b>PROJECT PURGED</b>\n\n" f"<code>{escape(destroyed['name'])}</code> has been erased from the matrix.", parse_mode="html", buttons=main_menu_markup(user.is_admin))
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
        lines = [f"🗂 <b>FILE VAULT — {escape(project.name)}</b>", ""]
        for item in files:
            lines.append(f"• <code>{escape(item.relative_path)}</code> — {'editable' if item.is_editable else 'binary/view only'}")
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=file_browser_markup([item.id for item in files], project.id, page, total_pages))
        return

    if data.startswith("file:view:"):
        _, _, file_id_raw, page_raw = data.split(":")
        file_id = int(file_id_raw)
        page = int(page_raw)
        async with session_scope() as session:
            project, project_file, chunk, current_page, total_pages = await file_manager_service.read_text_file_chunk(session, file_id, page)
            await get_owned_or_admin_project(session, user, project.id)
        await safe_event_edit(event, render_file_card(project, project_file, chunk, current_page, total_pages), parse_mode="html", buttons=file_view_markup(file_id, project.id, current_page, total_pages, project_file.is_editable))
        return

    if data.startswith("file:delete:") and not data.startswith("file:delete_confirm:"):
        file_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            project_file = await file_manager_service.get_file(session, file_id)
            if not project_file:
                await event.answer("File not found.", alert=True)
                return
            project = await get_owned_or_admin_project(session, user, project_file.project_id)
        await safe_event_edit(
            event,
            "🗑️ <b>FILE WIPE ARMED</b>\n\n"
            f"Target file: <code>{escape(project_file.relative_path)}</code>\n"
            "If confirmed, the file and its stored snapshots will be deleted from the node.",
            parse_mode="html",
            buttons=file_delete_confirm_markup(file_id, project.id),
        )
        return

    if data.startswith("file:delete_confirm:"):
        file_id = int(data.rsplit(":", 1)[-1])
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.id == user.id))
                project, project_file = await file_manager_service.delete_file(session, file_id)
                await get_owned_or_admin_project(session, db_user, project.id)
                await audit_service.record(
                    session,
                    event_type="FILE_DELETED",
                    summary=f"Deleted file {project_file.relative_path} from {project.name}",
                    severity="warning",
                    actor_user_id=db_user.id,
                    target_user_id=project.owner_user_id,
                    project_id=project.id,
                    payload={"relative_path": project_file.relative_path},
                )
            await safe_event_edit(event, "🗑️ <b>FILE PURGED</b>\n\n" f"<code>{escape(project_file.relative_path)}</code> has been removed from <b>{escape(project.name)}</b>.", parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        except Exception as exc:
            await event.answer(str(exc), alert=True)
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
            await event.answer("🕵️‍♂️ This asset is binary-only and cannot enter editor mode.", alert=True)
            return
        await state_store.set_editor_replace_wait(user.telegram_user_id, project_id=project.id, relative_path=project_file.relative_path, file_id=file_id)
        await safe_event_edit(event, "✏️ <b>REPLACE MODE ENGAGED</b>\n\n" f"Transmit the full replacement body for:\n<code>{escape(project_file.relative_path)}</code>\n\n" f"Payload ceiling: <code>{settings.max_editable_text_file_kb} KB</code>", parse_mode="html", buttons=file_view_markup(file_id, project.id, 0, 1, True))
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
            await event.answer("🕵️‍♂️ This asset cannot accept line patches.", alert=True)
            return
        await state_store.set_editor_patch_wait(user.telegram_user_id, project_id=project.id, relative_path=project_file.relative_path, file_id=file_id)
        await safe_event_edit(event, "🧩 <b>PATCH MODE ENGAGED</b>\n\nSend your patch in this format:\n<pre>12-18\n\nnew replacement lines here</pre>\nThe first line defines the target line range.", parse_mode="html", buttons=file_view_markup(file_id, project.id, 0, 1, True))
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
                    payload={"relative_path": state["relative_path"], "start_line": state["start_line"], "end_line": state["end_line"]},
                )
            await state_store.clear(user.telegram_user_id)
            await safe_event_edit(event, "✅ <b>PATCH COMMITTED</b>\n\n" f"Project status is now <code>{project.status.value}</code>.", parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        except Exception as exc:
            await state_store.clear(user.telegram_user_id)
            await event.answer(str(exc), alert=True)
        return

    if data == "file:patch_cancel":
        await state_store.clear(user.telegram_user_id)
        await safe_event_edit(event, "❌ <b>PATCH ABORTED</b>", parse_mode="html", buttons=main_menu_markup(user.is_admin))
        return

    if data.startswith("logs:view:"):
        _, _, project_id_raw, stream, filter_mode, page_raw = data.split(":")
        project_id = int(project_id_raw)
        page = int(page_raw)
        async with session_scope() as session:
            project = await get_owned_or_admin_project(session, user, project_id)
        text, current_page, total_pages, total_lines = logs_service.read_log_page(project, stream=stream, page=page, filter_mode=filter_mode)
        await safe_event_edit(event, render_log_page(project, stream, filter_mode, current_page, total_pages, total_lines, text), parse_mode="html", buttons=logs_view_markup(project.id, stream, filter_mode, current_page, total_pages))
        return

    if data == "admin:dashboard":
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        async with session_scope() as session:
            stats = await admin_service.dashboard_stats(session)
        text = (
            "🛡️ <b>ADMIN CONTROL :: CORE PANEL</b>\n\n"
            f"• Users: <b>{stats['total_users']}</b>\n"
            f"• Active Users: <b>{stats['active_users']}</b>\n"
            f"• Banned Users: <b>{stats['banned_users']}</b>\n"
            f"• Admin Keys: <b>{stats['total_admins']}</b>\n"
            f"• Projects: <b>{stats['total_projects']}</b>\n"
            f"• Pending Approvals: <b>{stats['pending_approvals']}</b>\n"
            f"• Running Projects: <b>{stats['running_projects']}</b>\n"
            f"• Error Projects: <b>{stats['errored_projects']}</b>\n"
            f"• Audit Events: <b>{stats['total_events']}</b>\n"
            f"• Force Subscribe: <code>{escape(stats['force_subscribe'] or 'OFF')}</code>"
        )
        await safe_event_edit(event, text, parse_mode="html", buttons=admin_dashboard_markup(bool(stats['force_subscribe'])))
        return

    if data == "admin:broadcast":
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        await state_store.set_state(user.telegram_user_id, {"kind": "awaiting_broadcast"})
        await safe_event_edit(event, "📣 <b>BROADCAST TERMINAL ARMED</b>\n\nSend the text payload now. It will be delivered to every ACTIVE user record.", parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data == "admin:fsub:set":
        await state_store.set_state(user.telegram_user_id, {"kind": "awaiting_fsub_channel"})
        await safe_event_edit(event, "🛡️ <b>FORCE SUBSCRIBE CONFIG</b>\n\nSend the target channel username or invite link now.", parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data == "admin:fsub:clear":
        async with session_scope() as session:
            await admin_service.clear_force_subscribe_channel(session)
            await audit_service.record(session, event_type="FSUB_REMOVED", summary="Removed force subscribe channel", severity="warning", actor_user_id=user.id)
        await safe_event_edit(event, "🛡️ <b>FORCE SUBSCRIBE DISABLED</b>\n\nThe command deck is now open without channel gating.", parse_mode="html", buttons=admin_dashboard_markup(False))
        return

    if data.startswith("admin:approvals:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        page = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            pending_projects, total_pages = await admin_service.list_pending_projects(session, page)
        if not pending_projects:
            await safe_event_edit(event, "✅ <b>QUEUE CLEAR</b>\n\nNo pending approvals remain.", parse_mode="html", buttons=admin_dashboard_markup())
            return
        project = pending_projects[0]
        text = f"🕒 <b>APPROVAL QUEUE</b>\n• Slot: <code>{page + 1}/{total_pages}</code>\n\n{render_project_card(project)}"
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
            await audit_service.record(session, event_type="PROJECT_APPROVED", summary=f"Approved project {project.name}", severity="info", actor_user_id=user.id, target_user_id=project.owner_user_id, project_id=project.id, payload={"queue_index": queue_index})
        if notification_service and target_user:
            await notification_service.notify_project_decision(target_user.telegram_user_id, project)
        await safe_event_edit(event, "✅ <b>DEPLOYMENT APPROVED</b>", parse_mode="html", buttons=admin_dashboard_markup())
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
            await audit_service.record(session, event_type="PROJECT_REJECTED", summary=f"Rejected project {project.name}", severity="warning", actor_user_id=user.id, target_user_id=project.owner_user_id, project_id=project.id, payload={"queue_index": queue_index})
        if notification_service and target_user:
            await notification_service.notify_project_decision(target_user.telegram_user_id, project)
        await safe_event_edit(event, "❌ <b>DEPLOYMENT REJECTED</b>", parse_mode="html", buttons=admin_dashboard_markup())
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
        lines = [f"🔍 <b>APPROVAL ANALYSIS — {escape(project.name)}</b>", f"Summary: {escape(project.analysis_summary or '-')}", ""]
        if not issues:
            lines.append("✅ No warnings are stored in the analysis payload.")
        else:
            for issue in issues:
                lines.append(f"• [{escape(str(issue.get('level')))}] {escape(str(issue.get('file')))}:{escape(str(issue.get('line') or '-'))} — {escape(str(issue.get('message')))}")
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=approval_markup(project.id, queue_index, total_pages))
        return

    if data.startswith("admin:users:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        page = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            users, total_pages = await admin_service.list_recent_users(session, page)
        lines = ["👥 <b>USER MATRIX</b>", ""]
        for item in users:
            lines.append(render_user_line(item))
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=admin_users_markup([item.telegram_user_id for item in users], page, total_pages))
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
            "👤 <b>USER DETAIL :: IDENTITY CARD</b>\n\n"
            f"• Username: @{escape(target.username or 'no_username')}\n"
            f"• Telegram ID: <code>{target.telegram_user_id}</code>\n"
            f"• Name: {escape((target.first_name or '-'))} {escape((target.last_name or ''))}\n"
            f"• Status: <b>{escape(target.status.value)}</b>\n"
            f"• Admin Key: <code>{'yes' if target.is_admin else 'no'}</code>\n"
            f"• Projects: <b>{project_count}</b>"
        )
        await safe_event_edit(event, text, parse_mode="html", buttons=admin_user_detail_markup(target.telegram_user_id, target.status == UserStatus.BANNED, target.is_admin))
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
        lines = [f"📁 <b>PROJECTS OF @{escape(target_user.username or 'no_username')}</b>", ""]
        for project in projects:
            lines.append(render_project_list_item(project))
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=user_projects_markup([project.id for project in projects], telegram_user_id, page, total_pages))
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
            target.banned_reason = "Blocked from admin control."
            await audit_service.record(session, event_type="USER_BANNED", summary=f"Banned user {telegram_user_id}", severity="warning", actor_user_id=user.id, target_user_id=target.id, payload={"telegram_user_id": telegram_user_id})
        await safe_event_edit(event, f"🚫 <b>USER BLOCKED</b>\n\n<code>{telegram_user_id}</code> has been disabled.", parse_mode="html", buttons=admin_dashboard_markup())
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
            await audit_service.record(session, event_type="USER_UNBANNED", summary=f"Unbanned user {telegram_user_id}", severity="info", actor_user_id=user.id, target_user_id=target.id, payload={"telegram_user_id": telegram_user_id})
        await safe_event_edit(event, f"♻️ <b>USER RESTORED</b>\n\n<code>{telegram_user_id}</code> is active again.", parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data.startswith("admin:promote_user:"):
        telegram_user_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            target = await admin_service.set_user_admin(session, telegram_user_id, True)
            await audit_service.record(session, event_type="USER_PROMOTED_ADMIN", summary=f"Promoted {telegram_user_id} to admin", severity="info", actor_user_id=user.id, target_user_id=target.id)
        await safe_event_edit(event, f"⬆️ <b>ADMIN KEY ISSUED</b>\n\n<code>{telegram_user_id}</code> is now an admin.", parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data.startswith("admin:demote_user:"):
        telegram_user_id = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            target = await admin_service.set_user_admin(session, telegram_user_id, False)
            await audit_service.record(session, event_type="USER_DEMOTED_ADMIN", summary=f"Demoted {telegram_user_id} from admin", severity="warning", actor_user_id=user.id, target_user_id=target.id)
        await safe_event_edit(event, f"⬇️ <b>ADMIN KEY REVOKED</b>\n\n<code>{telegram_user_id}</code> is now a standard user.", parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data.startswith("admin:projects_filter:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        _, _, status_filter, page_raw = data.split(":")
        page = int(page_raw)
        async with session_scope() as session:
            projects, total_pages = await admin_service.list_projects(session, status_filter=status_filter, page=page)
        lines = [f"📦 <b>PROJECT MATRIX — {escape(status_filter.upper())}</b>", ""]
        for item in projects:
            lines.append(render_project_list_item(item))
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=admin_projects_markup([item.id for item in projects], status_filter, page, total_pages))
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
        await safe_event_edit(event, render_project_card(project), parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=True))
        return

    if data == "admin:stats":
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        await safe_event_edit(event, render_node_status_card(admin_service.node_status()), parse_mode="html", buttons=admin_dashboard_markup())
        return

    if data.startswith("admin:activity:"):
        if not user.is_admin:
            await event.answer("Admin only.", alert=True)
            return
        page = int(data.rsplit(":", 1)[-1])
        async with session_scope() as session:
            events_list, total_pages = await admin_service.list_events(session, page)
        lines = ["🧾 <b>AUDIT STREAM</b>", ""]
        for item in events_list:
            lines.append(render_event_line(item))
        await safe_event_edit(event, "\n".join(lines), parse_mode="html", buttons=activity_markup(page, total_pages))
        return

    await event.answer("🕵️‍♂️ ERROR: command token not mapped inside the terminal.", alert=True)


@client.on(events.NewMessage)
async def message_router(event):
    global notification_service
    if event.raw_text and event.raw_text.startswith("/"):
        return

    user, _ = await upsert_user(event)
    if user.status != UserStatus.ACTIVE:
        return

    if not await ensure_force_subscribed(event, user, edit=False):
        return

    state = await state_store.get_state(user.telegram_user_id)
    if not state:
        return

    if state["kind"] == "awaiting_fsub_channel":
        if not user.is_admin:
            await state_store.clear(user.telegram_user_id)
            return
        if not event.raw_text:
            await event.reply("Send a channel username or invite link.")
            return
        async with session_scope() as session:
            await admin_service.set_force_subscribe_channel(session, event.raw_text.strip(), updated_by_user_id=user.id)
            await audit_service.record(session, event_type="FSUB_SET", summary=f"Force subscribe set to {event.raw_text.strip()}", severity="info", actor_user_id=user.id)
        await state_store.clear(user.telegram_user_id)
        await event.reply("🛡️ <b>FORCE SUBSCRIBE ENABLED</b>\n\nThe gate is now live.", parse_mode="html", buttons=admin_dashboard_markup(True))
        return

    if state["kind"] == "awaiting_broadcast":
        if not user.is_admin:
            await state_store.clear(user.telegram_user_id)
            return
        if not event.raw_text:
            await event.reply("Send the broadcast text payload as plain text.")
            return
        sent = 0
        failed = 0
        async with session_scope() as session:
            active_users = await admin_service.list_active_users(session)
        for target in active_users:
            try:
                await client.send_message(target.telegram_user_id, event.raw_text, parse_mode="html")
                sent += 1
            except Exception:
                failed += 1
        async with session_scope() as session:
            await audit_service.record(session, event_type="BROADCAST_SENT", summary=f"Broadcast delivered to {sent} users with {failed} failures", severity="info" if failed == 0 else "warning", actor_user_id=user.id, payload={"sent": sent, "failed": failed})
        await state_store.clear(user.telegram_user_id)
        await event.reply("📣 <b>BROADCAST COMPLETE</b>\n\n" f"• Delivered: <code>{sent}</code>\n" f"• Failed: <code>{failed}</code>", parse_mode="html", buttons=admin_dashboard_markup())
        return

    if state["kind"] == "awaiting_packages":
        if not event.raw_text:
            await event.reply("Send package names as plain text, separated by comma or new line.")
            return
        packages = parse_package_input(event.raw_text)
        if not packages:
            await event.reply("No package names detected. Send comma or newline separated package names.")
            return
        progress_message = await event.reply("⚡ <b>TERMINAL ACTIVE</b>", parse_mode="html")
        project = None
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.telegram_user_id == user.telegram_user_id))
                project = await get_owned_or_admin_project(session, db_user, int(state["project_id"]))
                runtime = state.get("runtime") or (project.runtime.value if hasattr(project.runtime, "value") else str(project.runtime))
                await animate_installation(progress_message, project.name)
                module_manager = ModuleManagerService(session)
                if runtime == "nodejs":
                    result = await module_manager.install_npm_packages(project.id, packages)
                    event_type = "NPM_PACKAGES_INSTALLED"
                    summary = f"Requested npm install for {len(packages)} package(s) in {project.name}"
                else:
                    result = await module_manager.install_pip_packages(project.id, packages)
                    event_type = "PIP_PACKAGES_INSTALLED"
                    summary = f"Requested pip install for {len(packages)} package(s) in {project.name}"
                await audit_service.record(session, event_type=event_type, summary=summary, severity="info" if result.get("success") else "warning", actor_user_id=db_user.id, target_user_id=project.owner_user_id, project_id=project.id, payload={"runtime": runtime, "packages": packages, "installed": result.get("installed", []), "failed": result.get("failed", []), "message": result.get("message")})
            await state_store.clear(user.telegram_user_id)
            await progress_message.edit(render_module_install_result(project, runtime, result), parse_mode="html", buttons=project_actions_markup(project.id, project.status.value, is_admin=user.is_admin))
        except Exception as exc:
            logger.exception("Package installation failed")
            await state_store.clear(user.telegram_user_id)
            buttons = project_actions_markup(project.id, project.status.value, is_admin=user.is_admin) if project else main_menu_markup(user.is_admin)
            await progress_message.edit("❌ <b>MODULE CONTROL FAILURE</b>\n\n<code>Install session aborted.</code>\n" f"Reason: <code>{escape(str(exc))}</code>", parse_mode="html", buttons=buttons)
        return

    if state["kind"] == "awaiting_upload":
        if not event.file:
            await event.reply("🕵️‍♂️ Upload rejected: transmit a file payload, not plain text.")
            return
        temp_dir = settings.temp_root / str(user.telegram_user_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        downloaded = await event.download_media(file=str(temp_dir))
        if not downloaded:
            await event.reply("❌ Node payload download failed.")
            return
        path = Path(downloaded)
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.telegram_user_id == user.telegram_user_id))
                project, analysis = await upload_service.create_project_from_upload(session, db_user, path, path.name)
                await audit_service.record(session, event_type="PROJECT_UPLOADED", summary=f"Uploaded project {project.name}", severity="info", actor_user_id=db_user.id, target_user_id=db_user.id, project_id=project.id, payload={"analysis_grade": analysis.grade.value})
            await state_store.clear(user.telegram_user_id)
            text = (
                f"🚀 <b>DEPLOYMENT PAYLOAD ACCEPTED</b>\n\n"
                f"• Project: <b>{escape(project.name)}</b>\n"
                f"• Status: <code>{project.status.value}</code>\n"
                f"• Analysis Grade: <b>{escape(analysis.grade.value)}</b>\n"
                f"• Signal: {escape(analysis.summary)}"
            )
            await event.reply(text, parse_mode="html", buttons=main_menu_markup(user.is_admin))
            if notification_service:
                await notification_service.notify_new_upload(user, project)
        except Exception as exc:
            logger.exception("Upload processing failed")
            await state_store.clear(user.telegram_user_id)
            await event.reply(f"❌ <b>DEPLOYMENT FAILURE</b>\n\n<code>{escape(str(exc))}</code>", parse_mode="html")
        return

    if state["kind"] == "awaiting_editor_replace":
        if not event.raw_text:
            await event.reply("Transmit plain text content for the replacement body.")
            return
        try:
            async with session_scope() as session:
                db_user = await session.scalar(select(User).where(User.telegram_user_id == user.telegram_user_id))
                project = await get_owned_or_admin_project(session, db_user, int(state["project_id"]))
                updated = await file_manager_service.overwrite_text_file(session, project.id, state["relative_path"], event.raw_text, db_user.id)
                await audit_service.record(session, event_type="FILE_REPLACED", summary=f"Replaced file {state['relative_path']} in {updated.name}", severity="info", actor_user_id=db_user.id, target_user_id=updated.owner_user_id, project_id=updated.id, payload={"relative_path": state["relative_path"]})
            await state_store.clear(user.telegram_user_id)
            await event.reply("✅ <b>FILE OVERWRITE COMMITTED</b>\n\n" f"Project status is now <code>{updated.status.value}</code>." + (" Re-approval is required before runtime ignition." if updated.status == ProjectStatus.PENDING_APPROVAL else ""), parse_mode="html", buttons=main_menu_markup(user.is_admin))
        except Exception as exc:
            logger.exception("Editor replace failed")
            await state_store.clear(user.telegram_user_id)
            await event.reply(f"❌ <b>EDITOR FAILURE</b>\n\n<code>{escape(str(exc))}</code>", parse_mode="html")
        return

    if state["kind"] == "awaiting_editor_patch":
        if not event.raw_text:
            await event.reply("Send patch text with the first line as `start-end`, then the replacement block.")
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
                _project_obj, project_file, _new_content, diff_preview = await file_manager_service.build_patch_preview(session, file_id=int(state["file_id"]), start_line=start_line, end_line=end_line, replacement_text=replacement_text)
            await state_store.set_patch_preview(user.telegram_user_id, project_id=project.id, relative_path=state["relative_path"], file_id=int(state["file_id"]), start_line=start_line, end_line=end_line, replacement_text=replacement_text, diff_preview=diff_preview)
            await event.reply(render_diff_preview(project_file.relative_path, start_line, end_line, diff_preview), parse_mode="html", buttons=patch_confirm_markup(int(state["file_id"]), project.id))
        except Exception as exc:
            logger.exception("Patch preview failed")
            await state_store.clear(user.telegram_user_id)
            await event.reply(f"❌ <b>PATCH PREVIEW FAILURE</b>\n\n<code>{escape(str(exc))}</code>", parse_mode="html")
        return


async def bootstrap() -> None:
    global notification_service, runtime_service, runtime_reconciler
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await client.start(bot_token=settings.bot_token)
    notification_service = NotificationService(client, settings)
    runtime_service = ProjectRuntimeService(settings, process_supervisor, notification_service, logs_service, audit_service)
    runtime_reconciler = RuntimeReconciler(settings, process_supervisor, runtime_service, audit_service, logs_service)
    reconcile_summary = await runtime_reconciler.reconcile()
    logger.info("Runtime reconciliation summary: %s", reconcile_summary)
    logger.info("Bot started")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(bootstrap())
