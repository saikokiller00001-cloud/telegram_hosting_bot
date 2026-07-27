from __future__ import annotations

from telethon import TelegramClient

from app.config import Settings
from app.db.models.project import Project
from app.db.models.user import User
from app.telegram.buttons import admin_user_quick_actions_markup, approval_markup, project_actions_markup


class NotificationService:
    def __init__(self, client: TelegramClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def notify_new_user(self, user: User) -> None:
        if not self.settings.notify_owner_on_new_user:
            return
        text = (
            "🆕 <b>New user joined</b>\n"
            f"• Username: @{user.username or 'no_username'}\n"
            f"• ID: <code>{user.telegram_user_id}</code>\n"
            f"• Name: {user.first_name or '-'} {user.last_name or ''}"
        )
        await self.client.send_message(
            self.settings.owner_telegram_id,
            text,
            parse_mode="html",
            buttons=admin_user_quick_actions_markup(user.telegram_user_id),
        )

    async def notify_new_upload(self, user: User, project: Project) -> None:
        if not self.settings.notify_owner_on_new_upload:
            return
        text = (
            "📦 <b>New project uploaded</b>\n"
            f"• User: @{user.username or 'no_username'} (<code>{user.telegram_user_id}</code>)\n"
            f"• Project: <b>{project.name}</b>\n"
            f"• File: <code>{project.upload_file_name}</code>\n"
            f"• Size: <code>{project.size_bytes}</code> bytes\n"
            f"• Analysis: <b>{project.analysis_grade.value}</b>\n"
            f"• Status: <b>{project.status.value}</b>"
        )
        await self.client.send_message(
            self.settings.owner_telegram_id,
            text,
            parse_mode="html",
            buttons=approval_markup(project.id, queue_index=0, queue_total=1),
        )

    async def notify_project_decision(self, user_telegram_id: int, project: Project) -> None:
        if project.status == project.status.APPROVED_STOPPED:
            text = f"✅ Your project <b>{project.name}</b> was approved. You can now run it from My Projects."
        else:
            text = (
                f"❌ Your project <b>{project.name}</b> was rejected.\n"
                f"Reason: <code>{project.rejection_reason or 'Admin review rejected it.'}</code>"
            )
        await self.client.send_message(user_telegram_id, text, parse_mode="html")

    async def notify_project_started(self, user_telegram_id: int, project: Project, pid: int | None) -> None:
        text = (
            f"▶️ <b>{project.name}</b> started.\n"
            f"• Status: <code>{project.status.value}</code>\n"
            f"• PID: <code>{pid or '-'}</code>"
        )
        await self.client.send_message(user_telegram_id, text, parse_mode="html")

    async def notify_project_stopped(self, user_telegram_id: int, project: Project, exit_code: int | None) -> None:
        text = (
            f"⏹ <b>{project.name}</b> stopped.\n"
            f"• Status: <code>{project.status.value}</code>\n"
            f"• Exit code: <code>{exit_code if exit_code is not None else '-'}</code>"
        )
        await self.client.send_message(user_telegram_id, text, parse_mode="html")

    async def notify_project_crashed(
        self,
        user_telegram_id: int,
        project: Project,
        stderr_tail: str | None,
        owner_summary: str | None = None,
    ) -> None:
        tail = (stderr_tail or "No stderr captured.")[:2000]
        user_text = (
            f"💥 <b>{project.name}</b> crashed.\n"
            f"• Status: <code>{project.status.value}</code>\n"
            f"• Error tail:\n<pre>{tail}</pre>"
        )
        await self.client.send_message(user_telegram_id, user_text, parse_mode="html")

        if self.settings.notify_owner_on_project_error:
            owner_text = (
                f"💥 <b>Project crashed</b>\n"
                f"• Project: <b>{project.name}</b>\n"
                f"• Summary: {owner_summary or 'Runtime exited unexpectedly.'}\n"
                f"• Error tail:\n<pre>{tail}</pre>"
            )
            await self.client.send_message(
                self.settings.owner_telegram_id,
                owner_text,
                parse_mode="html",
                buttons=project_actions_markup(project.id, project.status.value, is_admin=True),
            )
