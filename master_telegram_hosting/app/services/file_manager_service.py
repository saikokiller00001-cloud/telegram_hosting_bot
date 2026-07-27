from __future__ import annotations

import difflib
from math import ceil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.project import ChangeKind, FileVersion, Project, ProjectFile, ProjectStatus


class FileManagerService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def list_files_paginated(
        self,
        session: AsyncSession,
        project_id: int,
        page: int = 0,
        per_page: int | None = None,
    ) -> tuple[list[ProjectFile], int]:
        per_page = per_page or self.settings.page_size_files
        files = list(
            await session.scalars(
                select(ProjectFile)
                .where(ProjectFile.project_id == project_id)
                .order_by(ProjectFile.relative_path.asc())
            )
        )
        total_pages = max(1, ceil(len(files) / per_page))
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        end = start + per_page
        return files[start:end], total_pages

    async def get_file(self, session: AsyncSession, file_id: int) -> ProjectFile | None:
        return await session.scalar(select(ProjectFile).where(ProjectFile.id == file_id))

    async def read_text_file_chunk(
        self,
        session: AsyncSession,
        file_id: int,
        page: int = 0,
    ) -> tuple[Project, ProjectFile, str, int, int]:
        project_file = await self.get_file(session, file_id)
        if not project_file:
            raise ValueError("File not found.")

        project = await session.scalar(select(Project).where(Project.id == project_file.project_id))
        if not project:
            raise ValueError("Project not found.")

        path = Path(project.storage_path) / project_file.relative_path
        text = path.read_text(encoding="utf-8", errors="ignore")
        chunk_size = self.settings.file_page_chars
        total_pages = max(1, ceil(max(len(text), 1) / chunk_size))
        page = max(0, min(page, total_pages - 1))
        start = page * chunk_size
        end = start + chunk_size
        return project, project_file, text[start:end], page, total_pages

    async def overwrite_text_file(
        self,
        session: AsyncSession,
        project_id: int,
        relative_path: str,
        new_content: str,
        editor_user_id: int,
    ) -> Project:
        return await self._save_content(
            session,
            project_id=project_id,
            relative_path=relative_path,
            new_content=new_content,
            editor_user_id=editor_user_id,
            change_kind=ChangeKind.REPLACE,
        )

    async def build_patch_preview(
        self,
        session: AsyncSession,
        *,
        file_id: int,
        start_line: int,
        end_line: int,
        replacement_text: str,
    ) -> tuple[Project, ProjectFile, str, str]:
        project_file = await self.get_file(session, file_id)
        if not project_file or not project_file.is_editable:
            raise ValueError("Editable file not found.")
        project = await session.scalar(select(Project).where(Project.id == project_file.project_id))
        if not project:
            raise ValueError("Project not found.")

        path = Path(project.storage_path) / project_file.relative_path
        original_text = path.read_text(encoding="utf-8", errors="ignore")
        new_content, diff_preview = self._patch_text(original_text, start_line, end_line, replacement_text)
        return project, project_file, new_content, diff_preview

    async def apply_line_patch(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        relative_path: str,
        start_line: int,
        end_line: int,
        replacement_text: str,
        editor_user_id: int,
    ) -> Project:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        if not project:
            raise ValueError("Project not found.")
        path = Path(project.storage_path) / relative_path
        original_text = path.read_text(encoding="utf-8", errors="ignore")
        new_content, _ = self._patch_text(original_text, start_line, end_line, replacement_text)
        return await self._save_content(
            session,
            project_id=project_id,
            relative_path=relative_path,
            new_content=new_content,
            editor_user_id=editor_user_id,
            change_kind=ChangeKind.PATCH,
        )

    def _patch_text(self, original_text: str, start_line: int, end_line: int, replacement_text: str) -> tuple[str, str]:
        lines = original_text.splitlines()
        if start_line < 1 or end_line < start_line:
            raise ValueError("Invalid line range.")
        if lines and end_line > len(lines):
            raise ValueError(f"End line exceeds file length ({len(lines)}).")
        if not lines and start_line > 1:
            raise ValueError("Cannot patch beyond the end of an empty file.")

        replacement_lines = replacement_text.splitlines()
        before_lines = lines[start_line - 1 : end_line]
        after_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
        new_content = "\n".join(after_lines)
        if original_text.endswith("\n"):
            new_content += "\n"

        diff_preview = "\n".join(
            difflib.unified_diff(
                before_lines,
                replacement_lines,
                fromfile=f"before:{start_line}-{end_line}",
                tofile=f"after:{start_line}-{start_line + max(len(replacement_lines) - 1, 0)}",
                lineterm="",
            )
        )[:3500]
        return new_content, diff_preview

    async def _save_content(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        relative_path: str,
        new_content: str,
        editor_user_id: int,
        change_kind: ChangeKind,
    ) -> Project:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        project_file = await session.scalar(
            select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.relative_path == relative_path)
        )
        if not project or not project_file or not project_file.is_editable:
            raise ValueError("Editable file not found.")

        max_size_bytes = self.settings.max_editable_text_file_kb * 1024
        if len(new_content.encode("utf-8")) > max_size_bytes:
            raise ValueError(f"Edited content exceeds {self.settings.max_editable_text_file_kb} KB limit.")

        path = Path(project.storage_path) / relative_path
        old_content = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        path.write_text(new_content, encoding="utf-8")
        project_file.size_bytes = len(new_content.encode("utf-8"))

        version_no = (
            await session.scalar(select(func.max(FileVersion.version_no)).where(FileVersion.project_file_id == project_file.id))
            or 0
        ) + 1

        snapshot_path = self.settings.versions_root / project.slug / f"{project_file.id}_v{version_no}.snapshot"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(new_content, encoding="utf-8")

        diff_preview = "\n".join(
            difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )[:4000]

        session.add(
            FileVersion(
                project_file_id=project_file.id,
                version_no=version_no,
                editor_user_id=editor_user_id,
                change_kind=change_kind,
                content_snapshot_path=str(snapshot_path),
                diff_preview=diff_preview,
            )
        )

        if self.settings.auto_reapproval_on_edit:
            project.status = ProjectStatus.PENDING_APPROVAL
            session.add(
                Approval(
                    project_id=project.id,
                    requested_by_user_id=editor_user_id,
                    status=ApprovalStatus.PENDING,
                    decision_reason="Project edited after approval; re-review required.",
                )
            )
        return project
