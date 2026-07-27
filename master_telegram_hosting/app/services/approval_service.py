from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.project import Project, ProjectStatus


class ApprovalService:
    async def approve_project(self, session: AsyncSession, project_id: int, admin_user_id: int) -> Project:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        if not project:
            raise ValueError("Project not found.")

        project.status = ProjectStatus.APPROVED_STOPPED
        project.approved_by_user_id = admin_user_id

        approval = await session.scalar(
            select(Approval).where(Approval.project_id == project.id).order_by(Approval.id.desc())
        )
        if approval:
            approval.status = ApprovalStatus.APPROVED
            approval.reviewed_by_user_id = admin_user_id
            approval.decision_reason = "Approved from in-bot admin panel."
        return project

    async def reject_project(
        self,
        session: AsyncSession,
        project_id: int,
        admin_user_id: int,
        reason: str = "Rejected by admin review.",
    ) -> Project:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        if not project:
            raise ValueError("Project not found.")

        project.status = ProjectStatus.REJECTED
        project.rejection_reason = reason

        approval = await session.scalar(
            select(Approval).where(Approval.project_id == project.id).order_by(Approval.id.desc())
        )
        if approval:
            approval.status = ApprovalStatus.REJECTED
            approval.reviewed_by_user_id = admin_user_id
            approval.decision_reason = reason
        return project
