from __future__ import annotations

import os
import platform
import socket
import shutil
from math import ceil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.project import Project, ProjectStatus
from app.db.models.run_instance import RunInstance, RunStatus
from app.db.models.system import NotificationPreference, SystemEvent, SystemSetting
from app.db.models.user import User, UserStatus

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


class AdminService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def dashboard_stats(self, session: AsyncSession) -> dict:
        total_users = await session.scalar(select(func.count(User.id))) or 0
        active_users = await session.scalar(select(func.count(User.id)).where(User.status == UserStatus.ACTIVE)) or 0
        banned_users = await session.scalar(select(func.count(User.id)).where(User.status == UserStatus.BANNED)) or 0
        total_admins = await session.scalar(select(func.count(User.id)).where(User.is_admin.is_(True))) or 0
        total_projects = await session.scalar(select(func.count(Project.id)).where(Project.is_deleted.is_(False))) or 0
        pending_approvals = await session.scalar(select(func.count(Approval.id)).where(Approval.status == ApprovalStatus.PENDING)) or 0
        running_projects = await session.scalar(select(func.count(Project.id)).where(Project.status == ProjectStatus.RUNNING)) or 0
        stopped_projects = await session.scalar(select(func.count(Project.id)).where(Project.status == ProjectStatus.STOPPED)) or 0
        errored_projects = await session.scalar(select(func.count(Project.id)).where(Project.status == ProjectStatus.ERROR)) or 0
        active_runs = await session.scalar(select(func.count(RunInstance.id)).where(RunInstance.status == RunStatus.RUNNING)) or 0
        total_events = await session.scalar(select(func.count(SystemEvent.id))) or 0
        force_subscribe = await self.get_force_subscribe_channel(session)
        return {
            "total_users": total_users,
            "active_users": active_users,
            "banned_users": banned_users,
            "total_admins": total_admins,
            "total_projects": total_projects,
            "pending_approvals": pending_approvals,
            "running_projects": running_projects,
            "stopped_projects": stopped_projects,
            "errored_projects": errored_projects,
            "active_runs": active_runs,
            "total_events": total_events,
            "force_subscribe": force_subscribe,
        }

    async def list_recent_users(self, session: AsyncSession, page: int = 0) -> tuple[list[User], int]:
        users = list(await session.scalars(select(User).order_by(User.created_at.desc())))
        return self._paginate(users, page, self.settings.page_size_admin)

    async def list_pending_projects(self, session: AsyncSession, page: int = 0) -> tuple[list[Project], int]:
        projects = list(
            await session.scalars(
                select(Project).where(Project.status == ProjectStatus.PENDING_APPROVAL).order_by(Project.created_at.asc())
            )
        )
        return self._paginate(projects, page, 1)

    async def list_projects(
        self,
        session: AsyncSession,
        *,
        status_filter: str = "all",
        owner_user_id: int | None = None,
        page: int = 0,
    ) -> tuple[list[Project], int]:
        stmt = select(Project).where(Project.is_deleted.is_(False)).order_by(Project.created_at.desc())
        if owner_user_id is not None:
            stmt = stmt.where(Project.owner_user_id == owner_user_id)
        mapped_status = self._map_status(status_filter)
        if mapped_status:
            stmt = stmt.where(Project.status == mapped_status)
        projects = list(await session.scalars(stmt))
        return self._paginate(projects, page, self.settings.page_size_admin)

    async def list_user_projects(
        self,
        session: AsyncSession,
        telegram_user_id: int,
        page: int = 0,
    ) -> tuple[User | None, list[Project], int]:
        user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if not user:
            return None, [], 1
        projects, total_pages = await self.list_projects(session, owner_user_id=user.id, page=page)
        return user, projects, total_pages

    async def list_events(self, session: AsyncSession, page: int = 0) -> tuple[list[SystemEvent], int]:
        events = list(await session.scalars(select(SystemEvent).order_by(SystemEvent.created_at.desc())))
        return self._paginate(events, page, self.settings.page_size_admin)

    async def get_or_create_notification_pref(self, session: AsyncSession, user_id: int) -> NotificationPreference:
        pref = await session.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
        if pref:
            return pref
        pref = NotificationPreference(user_id=user_id)
        session.add(pref)
        await session.flush()
        return pref

    async def notifications_enabled(self, session: AsyncSession, user_id: int) -> bool:
        pref = await self.get_or_create_notification_pref(session, user_id)
        return all(
            [
                pref.notify_new_users,
                pref.notify_new_uploads,
                pref.notify_project_errors,
                pref.notify_pending_approvals,
                pref.notify_runtime_restarts,
            ]
        )

    async def toggle_notifications(self, session: AsyncSession, user_id: int) -> bool:
        pref = await self.get_or_create_notification_pref(session, user_id)
        enabled = not await self.notifications_enabled(session, user_id)
        pref.notify_new_users = enabled
        pref.notify_new_uploads = enabled
        pref.notify_project_errors = enabled
        pref.notify_pending_approvals = enabled
        pref.notify_runtime_restarts = enabled
        await session.flush()
        return enabled

    async def get_default_runtime(self, session: AsyncSession, user_id: int) -> str:
        row = await session.get(SystemSetting, f"user_runtime:{user_id}")
        runtime = ((row.value_json if row else {}) or {}).get("runtime", "python")
        return runtime if runtime in {"python", "nodejs"} else "python"

    async def set_default_runtime(self, session: AsyncSession, user_id: int, runtime: str, updated_by_user_id: int | None = None) -> str:
        if runtime not in {"python", "nodejs"}:
            raise ValueError("Unsupported runtime.")
        row = await session.get(SystemSetting, f"user_runtime:{user_id}")
        payload = {"runtime": runtime}
        if row:
            row.value_json = payload
            row.updated_by_user_id = updated_by_user_id
        else:
            row = SystemSetting(key=f"user_runtime:{user_id}", value_json=payload, updated_by_user_id=updated_by_user_id)
            session.add(row)
        await session.flush()
        return runtime

    async def get_billing_overview(self, session: AsyncSession, user: User) -> dict:
        project_count = await session.scalar(
            select(func.count(Project.id)).where(Project.owner_user_id == user.id, Project.is_deleted.is_(False))
        ) or 0
        storage_bytes = await session.scalar(
            select(func.coalesce(func.sum(Project.size_bytes), 0)).where(Project.owner_user_id == user.id, Project.is_deleted.is_(False))
        ) or 0
        notifications_enabled = await self.notifications_enabled(session, user.id)
        default_runtime = await self.get_default_runtime(session, user.id)
        project_limit = user.max_projects_override or self.settings.max_projects_per_user
        storage_limit_mb = user.max_file_size_mb_override or self.settings.max_upload_mb
        tier = "ROOT_ADMIN" if user.is_admin else ("BOOSTED" if user.max_projects_override or user.max_file_size_mb_override else "STANDARD")
        return {
            "tier": tier,
            "projects_used": int(project_count),
            "project_limit": int(project_limit),
            "storage_used_mb": round(float(storage_bytes) / (1024 * 1024), 2),
            "storage_limit_mb": int(storage_limit_mb),
            "max_upload_mb": int(self.settings.max_upload_mb),
            "notifications_enabled": notifications_enabled,
            "default_runtime": default_runtime,
        }

    async def get_force_subscribe_channel(self, session: AsyncSession) -> str | None:
        row = await session.get(SystemSetting, "force_subscribe")
        value = ((row.value_json if row else {}) or {}).get("channel")
        return str(value).strip() if value else None

    async def set_force_subscribe_channel(self, session: AsyncSession, channel_ref: str, updated_by_user_id: int | None = None) -> str:
        clean = channel_ref.strip()
        if not clean:
            raise ValueError("Channel reference cannot be empty.")
        row = await session.get(SystemSetting, "force_subscribe")
        payload = {"channel": clean}
        if row:
            row.value_json = payload
            row.updated_by_user_id = updated_by_user_id
        else:
            row = SystemSetting(key="force_subscribe", value_json=payload, updated_by_user_id=updated_by_user_id)
            session.add(row)
        await session.flush()
        return clean

    async def clear_force_subscribe_channel(self, session: AsyncSession) -> None:
        row = await session.get(SystemSetting, "force_subscribe")
        if row:
            await session.delete(row)
            await session.flush()

    async def set_user_admin(self, session: AsyncSession, telegram_user_id: int, value: bool) -> User:
        target = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if not target:
            raise ValueError("User not found.")
        target.is_admin = value
        await session.flush()
        return target

    async def list_active_users(self, session: AsyncSession) -> list[User]:
        return list(await session.scalars(select(User).where(User.status == UserStatus.ACTIVE).order_by(User.id.asc())))

    def node_status(self) -> dict:
        provider = "psutil" if psutil else "fallback"
        if psutil:
            vm = psutil.virtual_memory()
            du = psutil.disk_usage(str(self.settings.storage_root.resolve().anchor or "/"))
            boot_time = getattr(psutil, "boot_time", lambda: 0)()
            uptime_seconds = max(0, int(__import__('time').time() - boot_time)) if boot_time else 0
            load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
            return {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "cpu_percent": psutil.cpu_percent(interval=0.35),
                "memory_percent": vm.percent,
                "memory_used_mb": round(vm.used / (1024 * 1024)),
                "memory_total_mb": round(vm.total / (1024 * 1024)),
                "disk_percent": du.percent,
                "disk_used_gb": round(du.used / (1024**3), 2),
                "disk_total_gb": round(du.total / (1024**3), 2),
                "uptime": self._human_uptime(uptime_seconds),
                "load_avg": ", ".join(f"{value:.2f}" for value in load_avg),
                "provider": provider,
            }

        usage = shutil.disk_usage("/")
        load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        uptime_seconds = self._linux_uptime_seconds()
        return {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_used_mb": 0,
            "memory_total_mb": 0,
            "disk_percent": round((usage.used / usage.total) * 100, 2) if usage.total else 0.0,
            "disk_used_gb": round(usage.used / (1024**3), 2),
            "disk_total_gb": round(usage.total / (1024**3), 2),
            "uptime": self._human_uptime(uptime_seconds),
            "load_avg": ", ".join(f"{value:.2f}" for value in load_avg),
            "provider": provider,
        }

    def _map_status(self, status_filter: str) -> ProjectStatus | None:
        mapping = {
            "pending": ProjectStatus.PENDING_APPROVAL,
            "running": ProjectStatus.RUNNING,
            "stopped": ProjectStatus.STOPPED,
            "error": ProjectStatus.ERROR,
            "approved": ProjectStatus.APPROVED_STOPPED,
        }
        return mapping.get(status_filter)

    def _paginate(self, items: list, page: int, per_page: int) -> tuple[list, int]:
        total_pages = max(1, ceil(max(len(items), 1) / per_page))
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        end = start + per_page
        return items[start:end], total_pages

    @staticmethod
    def _linux_uptime_seconds() -> int:
        proc_uptime = Path('/proc/uptime')
        if proc_uptime.exists():
            try:
                return int(float(proc_uptime.read_text().split()[0]))
            except Exception:
                return 0
        return 0

    @staticmethod
    def _human_uptime(seconds: int) -> str:
        days, rem = divmod(max(0, seconds), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        chunks = []
        if days:
            chunks.append(f"{days}d")
        if hours:
            chunks.append(f"{hours}h")
        if minutes:
            chunks.append(f"{minutes}m")
        if secs or not chunks:
            chunks.append(f"{secs}s")
        return " ".join(chunks)
