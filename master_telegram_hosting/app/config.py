from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "telegram-hosting-bot"
    debug: bool = False

    api_id: int = Field(alias="API_ID")
    api_hash: str = Field(alias="API_HASH")
    bot_token: str = Field(alias="BOT_TOKEN")
    owner_telegram_id: int = Field(alias="OWNER_TELEGRAM_ID")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")

    storage_root: Path = Field(default=Path("storage"), alias="STORAGE_ROOT")
    quarantine_root: Path = Field(default=Path("storage/quarantine"), alias="QUARANTINE_ROOT")
    versions_root: Path = Field(default=Path("storage/versions"), alias="VERSIONS_ROOT")
    temp_root: Path = Field(default=Path("storage/temp"), alias="TEMP_ROOT")
    runtime_logs_root: Path = Field(default=Path("storage/runtime_logs"), alias="RUNTIME_LOGS_ROOT")

    max_projects_per_user: int = Field(default=3, alias="MAX_PROJECTS_PER_USER")
    max_upload_mb: int = Field(default=15, alias="MAX_UPLOAD_MB")
    max_zip_unpacked_mb: int = Field(default=50, alias="MAX_ZIP_UNPACKED_MB")
    max_zip_files: int = Field(default=200, alias="MAX_ZIP_FILES")
    max_editable_text_file_kb: int = Field(default=96, alias="MAX_EDITABLE_TEXT_FILE_KB")

    python_executable: str = Field(default="python3", alias="PYTHON_EXECUTABLE")
    node_executable: str = Field(default="node", alias="NODE_EXECUTABLE")

    runtime_stop_grace_seconds: int = Field(default=8, alias="RUNTIME_STOP_GRACE_SECONDS")
    runtime_stderr_tail_chars: int = Field(default=1800, alias="RUNTIME_STDERR_TAIL_CHARS")
    runtime_stdout_tail_chars: int = Field(default=1200, alias="RUNTIME_STDOUT_TAIL_CHARS")

    auto_reapproval_on_edit: bool = Field(default=True, alias="AUTO_REAPPROVAL_ON_EDIT")
    notify_owner_on_new_user: bool = Field(default=True, alias="NOTIFY_OWNER_ON_NEW_USER")
    notify_owner_on_new_upload: bool = Field(default=True, alias="NOTIFY_OWNER_ON_NEW_UPLOAD")
    notify_owner_on_project_error: bool = Field(default=True, alias="NOTIFY_OWNER_ON_PROJECT_ERROR")

    state_ttl_seconds: int = Field(default=900, alias="STATE_TTL_SECONDS")
    file_page_chars: int = Field(default=3000, alias="FILE_PAGE_CHARS")
    page_size_projects: int = Field(default=8, alias="PAGE_SIZE_PROJECTS")
    page_size_files: int = Field(default=8, alias="PAGE_SIZE_FILES")
    page_size_admin: int = Field(default=8, alias="PAGE_SIZE_ADMIN")

    systemd_run_command: str = Field(default="systemd-run", alias="SYSTEMD_RUN_COMMAND")
    systemctl_command: str = Field(default="systemctl", alias="SYSTEMCTL_COMMAND")
    systemd_use_sudo: bool = Field(default=False, alias="SYSTEMD_USE_SUDO")
    systemd_unit_prefix: str = Field(default="tg-host", alias="SYSTEMD_UNIT_PREFIX")
    systemd_poll_interval_seconds: int = Field(default=2, alias="SYSTEMD_POLL_INTERVAL_SECONDS")

    default_cpu_quota_pct: int = Field(default=50, alias="DEFAULT_CPU_QUOTA_PCT")
    default_memory_max_mb: int = Field(default=256, alias="DEFAULT_MEMORY_MAX_MB")
    default_runtime_max_sec: int = Field(default=0, alias="DEFAULT_RUNTIME_MAX_SEC")

    logs_max_bytes: int = Field(default=2_000_000, alias="LOGS_MAX_BYTES")
    logs_keep_files: int = Field(default=5, alias="LOGS_KEEP_FILES")
    logs_page_lines: int = Field(default=40, alias="LOGS_PAGE_LINES")
    crash_tail_lines: int = Field(default=25, alias="CRASH_TAIL_LINES")

    reconcile_restart_missing_units: bool = Field(default=True, alias="RECONCILE_RESTART_MISSING_UNITS")

    def ensure_dirs(self) -> None:
        for path in [
            self.storage_root,
            self.quarantine_root,
            self.versions_root,
            self.temp_root,
            self.runtime_logs_root,
        ]:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
