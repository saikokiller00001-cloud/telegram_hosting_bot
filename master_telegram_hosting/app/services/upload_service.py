from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.project import (
    AnalysisGrade,
    ChangeKind,
    FileKind,
    FileVersion,
    Project,
    ProjectFile,
    ProjectStatus,
    RuntimeKind,
)
from app.db.models.user import User
from app.services.analysis_service import AnalysisResult, AnalysisService


class UploadService:
    def __init__(self, settings: Settings, analysis_service: AnalysisService) -> None:
        self.settings = settings
        self.analysis_service = analysis_service

    async def create_project_from_upload(
        self,
        session: AsyncSession,
        user: User,
        source_path: Path,
        original_name: str,
    ) -> tuple[Project, AnalysisResult]:
        await self._enforce_user_project_limit(session, user)
        self._enforce_upload_size(source_path)

        runtime = self._detect_runtime(original_name)
        project_slug = f"{user.telegram_user_id}-{uuid4().hex[:8]}"
        project_name = Path(original_name).stem[:100]
        project_root = self.settings.storage_root / "projects" / project_slug
        project_root.mkdir(parents=True, exist_ok=True)

        entry_file = await self._place_project_files(source_path, original_name, project_root)
        analysis = await self.analysis_service.analyze_project(project_root, runtime, entry_file)
        sha256 = self.analysis_service.file_sha256(source_path)

        status = ProjectStatus.PENDING_APPROVAL if analysis.grade != AnalysisGrade.BLOCK else ProjectStatus.REJECTED_AUTOMATIC
        project = Project(
            owner_user_id=user.id,
            name=project_name,
            slug=project_slug,
            runtime=runtime,
            status=status,
            storage_path=str(project_root),
            entry_file=entry_file,
            upload_file_name=original_name,
            size_bytes=source_path.stat().st_size,
            sha256=sha256,
            analysis_grade=analysis.grade,
            analysis_summary=analysis.summary,
            analysis_report_json=analysis.as_json(),
            approval_required=True,
            rejection_reason="Automatically blocked by static analysis." if analysis.grade == AnalysisGrade.BLOCK else None,
        )
        session.add(project)
        await session.flush()

        await self._index_project_files(session, project, project_root, user.id)

        approval = Approval(
            project_id=project.id,
            requested_by_user_id=user.id,
            status=ApprovalStatus.REJECTED if analysis.grade == AnalysisGrade.BLOCK else ApprovalStatus.PENDING,
            decision_reason=project.rejection_reason,
            analysis_snapshot_json=analysis.as_json(),
        )
        session.add(approval)
        await session.flush()
        return project, analysis

    async def _enforce_user_project_limit(self, session: AsyncSession, user: User) -> None:
        limit = user.max_projects_override or self.settings.max_projects_per_user
        count = await session.scalar(
            select(func.count(Project.id)).where(
                Project.owner_user_id == user.id,
                Project.is_deleted.is_(False),
                Project.status != ProjectStatus.DELETED,
            )
        )
        if (count or 0) >= limit:
            raise ValueError(f"You already reached the project limit ({limit}).")

    def _enforce_upload_size(self, path: Path) -> None:
        max_bytes = self.settings.max_upload_mb * 1024 * 1024
        if path.stat().st_size > max_bytes:
            raise ValueError(f"Upload is larger than {self.settings.max_upload_mb} MB.")

    def _detect_runtime(self, original_name: str) -> RuntimeKind:
        suffix = Path(original_name).suffix.lower()
        if suffix == ".py":
            return RuntimeKind.PYTHON
        if suffix == ".js":
            return RuntimeKind.NODEJS
        if suffix == ".zip":
            return RuntimeKind.PYTHON
        raise ValueError("Only .py, .js, and .zip uploads are allowed.")

    async def _place_project_files(self, source_path: Path, original_name: str, project_root: Path) -> str:
        suffix = Path(original_name).suffix.lower()
        if suffix in {".py", ".js"}:
            target = project_root / Path(original_name).name
            shutil.copy2(source_path, target)
            return target.name

        if suffix == ".zip":
            return self._extract_zip_safely(source_path, project_root)

        raise ValueError("Unsupported upload type.")

    def _extract_zip_safely(self, source_path: Path, project_root: Path) -> str:
        unpacked_bytes = 0
        file_count = 0
        candidate_entry: str | None = None

        with zipfile.ZipFile(source_path) as archive:
            for member in archive.infolist():
                file_count += 1
                if file_count > self.settings.max_zip_files:
                    raise ValueError("Zip contains too many files.")

                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError("Zip contains unsafe paths.")

                unpacked_bytes += member.file_size
                if unpacked_bytes > self.settings.max_zip_unpacked_mb * 1024 * 1024:
                    raise ValueError("Zip expands beyond the allowed unpacked size.")

                archive.extract(member, project_root)
                lowered = member.filename.lower()
                if lowered.endswith("main.py") or lowered.endswith("bot.py"):
                    candidate_entry = member.filename
                if lowered.endswith("index.js") or lowered.endswith("app.js"):
                    candidate_entry = member.filename

        if not candidate_entry:
            for pattern in ["*.py", "*.js"]:
                first = next(project_root.rglob(pattern), None)
                if first:
                    candidate_entry = str(first.relative_to(project_root))
                    break

        if not candidate_entry:
            raise ValueError("No runnable .py or .js entry file was found inside the zip.")

        return candidate_entry

    async def _index_project_files(self, session: AsyncSession, project: Project, project_root: Path, editor_user_id: int) -> None:
        for path in project_root.rglob("*"):
            if not path.is_file():
                continue
            relative = str(path.relative_to(project_root))
            editable = path.suffix.lower() in {".py", ".js", ".txt", ".json", ".yaml", ".yml", ".env"}
            kind = FileKind.TEXT if editable else FileKind.BINARY
            project_file = ProjectFile(
                project_id=project.id,
                relative_path=relative,
                file_type=kind,
                size_bytes=path.stat().st_size,
                checksum=self.analysis_service.file_sha256(path),
                is_editable=editable,
            )
            session.add(project_file)
            await session.flush()

            snapshot_path = self.settings.versions_root / project.slug / f"{project_file.id}_v1.snapshot"
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, snapshot_path)
            session.add(
                FileVersion(
                    project_file_id=project_file.id,
                    version_no=1,
                    editor_user_id=editor_user_id,
                    change_kind=ChangeKind.UPLOAD,
                    content_snapshot_path=str(snapshot_path),
                )
            )
