from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.base import session_scope
from app.db.models.project import Project, ProjectStatus, RuntimeKind
from app.db.models.run_instance import RunInstance, RunStatus
from app.db.models.user import User
from app.runtime.process_supervisor import ProcessSupervisor
from app.runtime.systemd_launcher import RuntimeSecurityProfile
from app.services.audit_service import AuditService
from app.services.logs_service import LogsService
from app.services.notification_service import NotificationService


class ProjectRuntimeService:
    def __init__(
        self,
        settings: Settings,
        supervisor: ProcessSupervisor,
        notification_service: NotificationService,
        logs_service: LogsService,
        audit_service: AuditService,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.notification_service = notification_service
        self.logs_service = logs_service
        self.audit_service = audit_service

    async def _get_project(
        self,
        session: AsyncSession,
        actor: User,
        project_id: int,
    ) -> Project:
        project = await session.get(Project, project_id)
        if not project or project.is_deleted:
            raise ValueError("Project was not found.")
        if not actor:
            raise PermissionError("User was not found.")
        if not actor.is_admin and project.owner_user_id != actor.id:
            raise PermissionError("You do not have access to this project.")
        return project

    def _runtime_command(self, project: Project, root: Path) -> list[str]:
        entry = (root / project.entry_file).resolve()
        try:
            entry.relative_to(root)
        except ValueError as exc:
            raise ValueError("Invalid entrypoint path.") from exc

        if not entry.is_file():
            raise ValueError(f"Entrypoint file not found: {project.entry_file}")

        if project.runtime == RuntimeKind.PYTHON:
            return [self.settings.python_executable, "-u", str(entry)]
        if project.runtime == RuntimeKind.NODEJS:
            return [self.settings.node_executable, str(entry)]
        raise ValueError("Unsupported project runtime.")

    def _security_profile(self) -> RuntimeSecurityProfile:
        return RuntimeSecurityProfile(
            cpu_quota_pct=self.settings.default_cpu_quota_pct,
            memory_max_mb=self.settings.default_memory_max_mb,
            runtime_max_sec=self.settings.default_runtime_max_sec,
        )

    async def start_project(
        self,
        session: AsyncSession,
        actor: User,
        project_id: int,
        *,
        notify_user: bool = True,
        recovery: bool = False,
    ) -> Project:
        project = await self._get_project(session, actor, project_id)

        runnable = {
            ProjectStatus.APPROVED_STOPPED,
            ProjectStatus.STOPPED,
            ProjectStatus.ERROR,
        }
        if project.status == ProjectStatus.RUNNING and not recovery:
            raise ValueError("Project is already running.")
        if project.status not in runnable and not (
            recovery and project.status == ProjectStatus.RUNNING
        ):
            raise ValueError(f"Project cannot be started from {project.status.value}.")

        root = Path(project.storage_path).resolve()
        if not root.is_dir():
            raise ValueError("Project storage directory does not exist.")

        command = self._runtime_command(project, root)
        self.logs_service.rotate_project_logs(project.slug)
        stdout_path = self.logs_service.stdout_path(project.slug)
        stderr_path = self.logs_service.stderr_path(project.slug)

        run = RunInstance(
            project_id=project.id,
            requested_by_user_id=actor.id,
            status=RunStatus.STARTING,
            runtime_meta_json={"runtime": project.runtime.value},
        )
        session.add(run)
        await session.flush()

        async def on_exit(
            exit_code: int,
            out_path: Path,
            err_path: Path,
            stop_requested: bool,
            unit_name: str,
        ) -> None:
            await self._handle_unit_exit(
                project.id,
                run.id,
                project.owner_user_id,
                exit_code,
                out_path,
                err_path,
                stop_requested,
                unit_name,
            )

        try:
            result = await self.supervisor.start(
                project_id=project.id,
                command=command,
                cwd=root,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                profile=self._security_profile(),
                env={"PYTHONUNBUFFERED": "1"}
                if project.runtime == RuntimeKind.PYTHON
                else None,
                on_exit=on_exit,
            )
        except Exception as exc:
            run.status = RunStatus.FAILED_TO_START
            run.ended_at = datetime.now(timezone.utc)
            run.stderr_tail = str(exc)[-self.settings.runtime_stderr_tail_chars :]
            project.status = ProjectStatus.ERROR
            await self.audit_service.record(
                session,
                event_type="RUNTIME_START_FAILED",
                summary=f"Failed to start project {project.name}: {exc}",
                severity="error",
                actor_user_id=actor.id,
                target_user_id=project.owner_user_id,
                project_id=project.id,
                run_instance_id=run.id,
            )
            await session.flush()
            raise

        run.unit_name = result.get("UnitName")
        try:
            run.pid = int(result.get("MainPID", "0") or 0) or None
        except (TypeError, ValueError):
            run.pid = None
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        project.status = ProjectStatus.RUNNING

        await self.audit_service.record(
            session,
            event_type="RUNTIME_STARTED",
            summary=f"Started project {project.name}",
            actor_user_id=actor.id,
            target_user_id=project.owner_user_id,
            project_id=project.id,
            run_instance_id=run.id,
        )
        await session.flush()

        if notify_user:
            owner = await session.get(User, project.owner_user_id)
            if owner:
                try:
                    await self.notification_service.notify_project_started(
                        owner.telegram_user_id,
                        project,
                        run.pid,
                    )
                except Exception:
                    pass

        return project

    async def stop_project(
        self,
        session: AsyncSession,
        actor: User,
        project_id: int,
        *,
        notify_user: bool = True,
    ) -> Project:
        project = await self._get_project(session, actor, project_id)
        if project.status != ProjectStatus.RUNNING:
            raise ValueError("Project is not running.")

        run = await session.scalar(
            select(RunInstance)
            .where(RunInstance.project_id == project.id)
            .order_by(RunInstance.id.desc())
        )
        if run:
            run.status = RunStatus.STOPPING
        await session.flush()

        exit_code = await self.supervisor.stop(project.id)
        if exit_code is None:
            if run:
                run.status = RunStatus.KILLED
                run.ended_at = datetime.now(timezone.utc)
            project.status = ProjectStatus.ERROR
            await session.flush()
            raise RuntimeError("Project did not stop within the grace period.")

        if run:
            await session.refresh(run)

        should_notify = bool(run and run.status == RunStatus.STOPPING)
        if run and should_notify:
            run.status = RunStatus.STOPPED
            run.ended_at = datetime.now(timezone.utc)
            run.exit_code = exit_code

        project.status = ProjectStatus.STOPPED
        await self.audit_service.record(
            session,
            event_type="RUNTIME_STOPPED",
            summary=f"Stopped project {project.name}",
            actor_user_id=actor.id,
            target_user_id=project.owner_user_id,
            project_id=project.id,
            run_instance_id=run.id if run else None,
        )
        await session.flush()

        if notify_user and should_notify:
            owner = await session.get(User, project.owner_user_id)
            if owner:
                try:
                    await self.notification_service.notify_project_stopped(
                        owner.telegram_user_id,
                        project,
                        exit_code,
                    )
                except Exception:
                    pass

        return project

    async def restart_project(
        self,
        session: AsyncSession,
        actor: User,
        project_id: int,
    ) -> Project:
        await self.stop_project(
            session,
            actor,
            project_id,
            notify_user=False,
        )
        return await self.start_project(
            session,
            actor,
            project_id,
            notify_user=True,
        )

    async def _handle_unit_exit(
        self,
        project_id: int,
        run_id: int,
        owner_user_id: int,
        exit_code: int,
        stdout_path: Path,
        stderr_path: Path,
        stop_requested: bool,
        unit_name: str,
    ) -> None:
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            run = await session.get(RunInstance, run_id)
            if not project or not run:
                return

            if run.status in {
                RunStatus.STOPPED,
                RunStatus.KILLED,
                RunStatus.CRASHED,
                RunStatus.FAILED_TO_START,
            }:
                return

            run.unit_name = unit_name
            run.exit_code = exit_code
            run.ended_at = datetime.now(timezone.utc)
            run.stdout_tail = self._read_tail(
                stdout_path,
                self.settings.runtime_stdout_tail_chars,
            )
            run.stderr_tail = self._read_tail(
                stderr_path,
                self.settings.runtime_stderr_tail_chars,
            )

            latest_run = await session.scalar(
                select(RunInstance)
                .where(RunInstance.project_id == project_id)
                .order_by(RunInstance.id.desc())
            )
            is_current = latest_run is not None and latest_run.id == run.id

            if stop_requested or exit_code == 0:
                run.status = RunStatus.STOPPED
                if is_current:
                    project.status = ProjectStatus.STOPPED
                event_type = "RUNTIME_STOPPED"
            else:
                run.status = RunStatus.CRASHED
                if is_current:
                    project.status = ProjectStatus.ERROR
                event_type = "RUNTIME_CRASHED"

            await self.audit_service.record(
                session,
                event_type=event_type,
                summary=f"Runtime exited for project {project.name}",
                severity="error" if event_type == "RUNTIME_CRASHED" else "info",
                target_user_id=owner_user_id,
                project_id=project.id,
                run_instance_id=run.id,
                payload={"exit_code": exit_code},
            )
            await session.flush()

            if not is_current:
                return

            owner = await session.get(User, owner_user_id)
            if not owner:
                return

            try:
                if run.status == RunStatus.STOPPED:
                    await self.notification_service.notify_project_stopped(
                        owner.telegram_user_id,
                        project,
                        exit_code,
                    )
                else:
                    await self.notification_service.notify_project_crashed(
                        owner.telegram_user_id,
                        project,
                        run.stderr_tail,
                    )
            except Exception:
                pass

    @staticmethod
    def _read_tail(path: Path, char_count: int) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(
                encoding="utf-8",
                errors="ignore",
            )[-char_count:]
        except OSError:
            return ""
