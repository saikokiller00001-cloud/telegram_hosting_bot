from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RunStatus(str, enum.Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    CRASHED = "CRASHED"
    FAILED_TO_START = "FAILED_TO_START"
    KILLED = "KILLED"


class RunInstance(Base, TimestampMixin):
    __tablename__ = "run_instances"
    __table_args__ = (
        Index("ix_runs_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.STARTING, index=True)
    unit_name: Mapped[str | None] = mapped_column(String(140), nullable=True, index=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host_node: Mapped[str] = mapped_column(String(64), default="local-vps-1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_meta_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    restart_count: Mapped[int] = mapped_column(default=0)
