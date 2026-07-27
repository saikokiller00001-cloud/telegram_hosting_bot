from __future__ import annotations

import enum
from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RuntimeKind(str, enum.Enum):
    PYTHON = "python"
    NODEJS = "nodejs"


class AnalysisGrade(str, enum.Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class ProjectStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    ANALYZED = "ANALYZED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED_STOPPED = "APPROVED_STOPPED"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    REJECTED = "REJECTED"
    REJECTED_AUTOMATIC = "REJECTED_AUTOMATIC"
    DISABLED = "DISABLED"
    DELETED = "DELETED"


class FileKind(str, enum.Enum):
    TEXT = "text"
    BINARY = "binary"
    ARCHIVE = "archive"
    CONFIG = "config"
    UNKNOWN = "unknown"


class ChangeKind(str, enum.Enum):
    UPLOAD = "upload"
    REPLACE = "replace"
    PATCH = "patch"
    RENAME = "rename"


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True)
    runtime: Mapped[RuntimeKind] = mapped_column(Enum(RuntimeKind))
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), default=ProjectStatus.UPLOADED, index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    entry_file: Mapped[str] = mapped_column(String(255))
    upload_file_name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    analysis_grade: Mapped[AnalysisGrade] = mapped_column(Enum(AnalysisGrade), default=AnalysisGrade.WARN)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approval_required: Mapped[bool] = mapped_column(default=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(default=False)


class ProjectFile(Base, TimestampMixin):
    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("project_id", "relative_path", name="uq_project_file_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    relative_path: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[FileKind] = mapped_column(Enum(FileKind), default=FileKind.UNKNOWN)
    size_bytes: Mapped[int] = mapped_column(default=0)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_editable: Mapped[bool] = mapped_column(default=False)


class FileVersion(Base):
    __tablename__ = "file_versions"
    __table_args__ = (
        Index("ix_file_versions_file_version_no", "project_file_id", "version_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_file_id: Mapped[int] = mapped_column(ForeignKey("project_files.id"), index=True)
    version_no: Mapped[int] = mapped_column(default=1)
    editor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    change_kind: Mapped[ChangeKind] = mapped_column(Enum(ChangeKind), default=ChangeKind.UPLOAD)
    content_snapshot_path: Mapped[str] = mapped_column(String(500))
    diff_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
