from __future__ import annotations

from math import ceil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.project import Project, ProjectStatus
from app.db.models.run_instance import RunInstance, RunStatus
from app.db.models.system import SystemEvent
from app.db.models.user import User, UserStatus


class AdminService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def dashboard_stats(self, session: AsyncSession) -> dict:
        total_users = await session.scalar(select(func.count(User.id))) or 0
        active_users = await session.scalar(select(func.count(User.id)).where(User.status == UserStatus.ACTIVE)) or 0
        banned_users = await session.scalar(select(func.count(User.id)).where(User.status == UserStatus.BANNED)) or 0
        total_projects = await session.scalar(select(func.count(Project.id)).where(Project.is_deleted.is_(False))) or 0
        pending_approvals = await session.scalar(select(func.count(Approval.id)).where(Approval.status == ApprovalStatus.PENDING)) or 0
        running_projects = await session.scalar(select(func.count(Project.id)).where(Project.status == ProjectStatus.RUNNING)) or 0
        stopped_projects = await session.scalar(select(func.count(Project.id)).where(Project.status == ProjectStatus.STOPPED)) or 0
        errored_projects = await session.scalar(select(func.count(Project.id)).where(Project.status == ProjectStatus.ERROR)) or 0
        active_runs = await session.scalar(select(func.count(RunInstance.id)).where(RunInstance.status == RunStatus.RUNNING)) or 0
        total_events = await session.scalar(select(func.count(SystemEvent.id))) or 0
        return {
            "total_users": total_users,
            "active_users": active_users,
            "banned_users": banned_users,
            "total_projects": total_projects,
            "pending_approvals": pending_approvals,
            "running_projects": running_projects,
            "stopped_projects": stopped_projects,
            "errored_projects": errored_projects,
            "active_runs": active_runs,
            "total_events": total_events,
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
