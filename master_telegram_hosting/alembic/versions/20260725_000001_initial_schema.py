"""initial schema

Revision ID: 20260725_000001
Revises: None
Create Date: 2026-07-25 02:55:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260725_000001"
down_revision = None
branch_labels = None
depends_on = None


user_status = sa.Enum("ACTIVE", "BANNED", "DISABLED", name="userstatus")
runtime_kind = sa.Enum("python", "nodejs", name="runtimekind")
analysis_grade = sa.Enum("PASS", "WARN", "BLOCK", name="analysisgrade")
project_status = sa.Enum(
    "UPLOADED",
    "ANALYZED",
    "PENDING_APPROVAL",
    "APPROVED_STOPPED",
    "RUNNING",
    "STOPPED",
    "ERROR",
    "REJECTED",
    "REJECTED_AUTOMATIC",
    "DISABLED",
    "DELETED",
    name="projectstatus",
)
file_kind = sa.Enum("text", "binary", "archive", "config", "unknown", name="filekind")
change_kind = sa.Enum("upload", "replace", "patch", "rename", name="changekind")
approval_status = sa.Enum("PENDING", "APPROVED", "REJECTED", "OVERRIDDEN", name="approvalstatus")
run_status = sa.Enum(
    "STARTING",
    "RUNNING",
    "STOPPING",
    "STOPPED",
    "CRASHED",
    "FAILED_TO_START",
    "KILLED",
    name="runstatus",
)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", user_status, nullable=False, server_default="ACTIVE"),
        sa.Column("max_projects_override", sa.Integer(), nullable=True),
        sa.Column("max_file_size_mb_override", sa.Integer(), nullable=True),
        sa.Column("banned_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"])
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("runtime", runtime_kind, nullable=False),
        sa.Column("status", project_status, nullable=False, server_default="UPLOADED"),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("entry_file", sa.String(length=255), nullable=False),
        sa.Column("upload_file_name", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("analysis_grade", analysis_grade, nullable=False, server_default="WARN"),
        sa.Column("analysis_summary", sa.Text(), nullable=True),
        sa.Column("analysis_report_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_projects_owner_user_id", "projects", ["owner_user_id"])
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_sha256", "projects", ["sha256"])
    op.create_index("ix_projects_owner_status", "projects", ["owner_user_id", "status"])

    op.create_table(
        "project_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("file_type", file_kind, nullable=False, server_default="unknown"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("is_editable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("project_id", "relative_path", name="uq_project_file_path"),
    )
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])

    op.create_table(
        "file_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_file_id", sa.Integer(), sa.ForeignKey("project_files.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("editor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("change_kind", change_kind, nullable=False, server_default="upload"),
        sa.Column("content_snapshot_path", sa.String(length=500), nullable=False),
        sa.Column("diff_preview", sa.Text(), nullable=True),
    )
    op.create_index("ix_file_versions_project_file_id", "file_versions", ["project_file_id"])
    op.create_index("ix_file_versions_editor_user_id", "file_versions", ["editor_user_id"])
    op.create_index("ix_file_versions_file_version_no", "file_versions", ["project_file_id", "version_no"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", approval_status, nullable=False, server_default="PENDING"),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("analysis_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_approvals_project_id", "approvals", ["project_id"])
    op.create_index("ix_approvals_requested_by_user_id", "approvals", ["requested_by_user_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_status_created", "approvals", ["status", "created_at"])

    op.create_table(
        "run_instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="STARTING"),
        sa.Column("unit_name", sa.String(length=140), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("process_group_id", sa.Integer(), nullable=True),
        sa.Column("host_node", sa.String(length=64), nullable=False, server_default="local-vps-1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
        sa.Column("stderr_tail", sa.Text(), nullable=True),
        sa.Column("runtime_meta_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("restart_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_run_instances_project_id", "run_instances", ["project_id"])
    op.create_index("ix_run_instances_requested_by_user_id", "run_instances", ["requested_by_user_id"])
    op.create_index("ix_run_instances_status", "run_instances", ["status"])
    op.create_index("ix_run_instances_unit_name", "run_instances", ["unit_name"])
    op.create_index("ix_runs_project_status", "run_instances", ["project_id", "status"])

    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="info"),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("run_instance_id", sa.Integer(), sa.ForeignKey("run_instances.id"), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_system_events_event_type", "system_events", ["event_type"])
    op.create_index("ix_system_events_type_created", "system_events", ["event_type", "created_at"])
    op.create_index("ix_system_events_project_created", "system_events", ["project_id", "created_at"])

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notify_new_users", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notify_new_uploads", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notify_project_errors", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notify_pending_approvals", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notify_runtime_restarts", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("notification_preferences")
    op.drop_index("ix_system_events_project_created", table_name="system_events")
    op.drop_index("ix_system_events_type_created", table_name="system_events")
    op.drop_index("ix_system_events_event_type", table_name="system_events")
    op.drop_table("system_events")
    op.drop_index("ix_runs_project_status", table_name="run_instances")
    op.drop_index("ix_run_instances_unit_name", table_name="run_instances")
    op.drop_index("ix_run_instances_status", table_name="run_instances")
    op.drop_index("ix_run_instances_requested_by_user_id", table_name="run_instances")
    op.drop_index("ix_run_instances_project_id", table_name="run_instances")
    op.drop_table("run_instances")
    op.drop_index("ix_approvals_status_created", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_requested_by_user_id", table_name="approvals")
    op.drop_index("ix_approvals_project_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_file_versions_file_version_no", table_name="file_versions")
    op.drop_index("ix_file_versions_editor_user_id", table_name="file_versions")
    op.drop_index("ix_file_versions_project_file_id", table_name="file_versions")
    op.drop_table("file_versions")
    op.drop_index("ix_project_files_project_id", table_name="project_files")
    op.drop_table("project_files")
    op.drop_index("ix_projects_owner_status", table_name="projects")
    op.drop_index("ix_projects_sha256", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_owner_user_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    run_status.drop(bind, checkfirst=True)
    approval_status.drop(bind, checkfirst=True)
    change_kind.drop(bind, checkfirst=True)
    file_kind.drop(bind, checkfirst=True)
    project_status.drop(bind, checkfirst=True)
    analysis_grade.drop(bind, checkfirst=True)
    runtime_kind.drop(bind, checkfirst=True)
    user_status.drop(bind, checkfirst=True)
