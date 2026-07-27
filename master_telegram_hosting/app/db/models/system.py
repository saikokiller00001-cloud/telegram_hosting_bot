from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SystemEvent(Base, TimestampMixin):
    __tablename__ = "system_events"
    __table_args__ = (
        Index("ix_system_events_type_created", "event_type", "created_at"),
        Index("ix_system_events_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(20), default="info")
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    run_instance_id: Mapped[int | None] = mapped_column(ForeignKey("run_instances.id"), nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class NotificationPreference(Base, TimestampMixin):
    __tablename__ = "notification_preferences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    notify_new_users: Mapped[bool] = mapped_column(default=True)
    notify_new_uploads: Mapped[bool] = mapped_column(default=True)
    notify_project_errors: Mapped[bool] = mapped_column(default=True)
    notify_pending_approvals: Mapped[bool] = mapped_column(default=True)
    notify_runtime_restarts: Mapped[bool] = mapped_column(default=True)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSONB)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
