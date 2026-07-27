from __future__ import annotations

from sqlalchemy import desc, select

from app.config import Settings
from app.db.base import session_scope
from app.db.models.project import Project, ProjectStatus
from app.db.models.run_instance import RunInstance, RunStatus
from app.db.models.user import User
from app.runtime.process_supervisor import ProcessSupervisor
from app.services.audit_service import AuditService
from app.services.logs_service import LogsService
from app.services.project_runtime_service import ProjectRuntimeService


class RuntimeReconciler:
    def __init__(
        self,
        settings: Settings,
        supervisor: ProcessSupervisor,
        runtime_service: ProjectRuntimeService,
        audit_service: AuditService,
        logs_service: LogsService,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.runtime_service = runtime_service
        self.audit_service = audit_service
        self.logs_service = logs_service

    async def reconcile(self) -> dict[str, int]:
        summary = {"healthy": 0, "restarted": 0, "marked_error": 0, "adopted": 0}
        async with session_scope() as session:
            projects = list(
                await session.scalars(
                    select(Project).where(Project.status == ProjectStatus.RUNNING).order_by(Project.id.asc())
                )
            )
            for project in projects:
                status = await self.supervisor.status(project.id)
                latest_run = await session.scalar(
                    select(RunInstance)
                    .where(RunInstance.project_id == project.id)
                    .order_by(desc(RunInstance.id))
                )

                if self.supervisor.launcher.is_active(status):
                    if latest_run:
                        latest_run.pid = int(status.get("MainPID", "0") or 0) or None
                        latest_run.unit_name = status.get("Id")
                        latest_run.status = RunStatus.RUNNING
                        stdout_path = self.logs_service.stdout_path(project.slug)
                        stderr_path = self.logs_service.stderr_path(project.slug)
                        adopted = await self.supervisor.adopt_existing(
                            project_id=project.id,
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            on_exit=lambda code, out_path, err_path, stop_requested, unit_name, p=project.id, r=latest_run.id, o=project.owner_user_id: self.runtime_service._handle_unit_exit(  # noqa: SLF001
                                p,
                                r,
                                o,
                                code,
                                out_path,
                                err_path,
                                stop_requested,
                                unit_name,
                            ),
                        )
                        if adopted:
                            summary["adopted"] += 1
                    summary["healthy"] += 1
                    continue

                owner = await session.scalar(select(User).where(User.id == project.owner_user_id))
                if not owner:
                    project.status = ProjectStatus.ERROR
                    summary["marked_error"] += 1
                    continue

                if self.settings.reconcile_restart_missing_units:
                    await self.audit_service.record(
                        session,
                        event_type="RUNTIME_RECONCILE_RESTART",
                        summary=f"Reconciler restarted missing unit for project {project.name}",
                        severity="warning",
                        actor_user_id=owner.id,
                        target_user_id=owner.id,
                        project_id=project.id,
                        payload={"reason": "unit missing after bot restart"},
                    )
                    await self.runtime_service.start_project(
                        session,
                        owner,
                        project.id,
                        notify_user=False,
                        recovery=True,
                    )
                    summary["restarted"] += 1
                else:
                    project.status = ProjectStatus.ERROR
                    await self.audit_service.record(
                        session,
                        event_type="RUNTIME_RECONCILE_MARK_ERROR",
                        summary=f"Reconciler marked {project.name} as error because unit was missing",
                        severity="error",
                        actor_user_id=owner.id,
                        target_user_id=owner.id,
                        project_id=project.id,
                        payload={"reason": "unit missing after bot restart"},
                    )
                    summary["marked_error"] += 1
        return summary
