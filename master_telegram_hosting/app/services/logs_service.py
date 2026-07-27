from __future__ import annotations

from math import ceil
from pathlib import Path

from app.config import Settings
from app.db.models.project import Project


class LogsService:
    FILTER_KEYWORDS = {
        "errors": ["error", "warning", "traceback", "exception", "fatal", "critical"],
        "all": [],
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def stdout_path(self, project_slug: str) -> Path:
        path = self.settings.runtime_logs_root / project_slug
        path.mkdir(parents=True, exist_ok=True)
        return path / "stdout.log"

    def stderr_path(self, project_slug: str) -> Path:
        path = self.settings.runtime_logs_root / project_slug
        path.mkdir(parents=True, exist_ok=True)
        return path / "stderr.log"

    def rotate_project_logs(self, project_slug: str) -> None:
        for path in [self.stdout_path(project_slug), self.stderr_path(project_slug)]:
            self._rotate_file_if_needed(path)

    def _rotate_file_if_needed(self, path: Path) -> None:
        if not path.exists() or path.stat().st_size < self.settings.logs_max_bytes:
            return
        keep = max(1, self.settings.logs_keep_files)
        oldest = path.with_name(f"{path.name}.{keep}")
        if oldest.exists():
            oldest.unlink()
        for idx in range(keep - 1, 0, -1):
            current = path.with_name(f"{path.name}.{idx}")
            nxt = path.with_name(f"{path.name}.{idx + 1}")
            if current.exists():
                current.replace(nxt)
        path.replace(path.with_name(f"{path.name}.1"))

    def read_log_page(
        self,
        project: Project,
        *,
        stream: str = "stderr",
        page: int = 0,
        filter_mode: str = "all",
    ) -> tuple[str, int, int, int]:
        path = self.stdout_path(project.slug) if stream == "stdout" else self.stderr_path(project.slug)
        if not path.exists():
            return "(log is empty)", 0, 1, 0

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        filtered = self._apply_filter(lines, filter_mode)
        per_page = self.settings.logs_page_lines
        total_pages = max(1, ceil(max(len(filtered), 1) / per_page))
        page = max(0, min(page, total_pages - 1))

        end = len(filtered) - (page * per_page)
        start = max(0, end - per_page)
        chunk = filtered[start:end]
        if not chunk:
            return "(no lines match this filter)", page, total_pages, len(filtered)
        return "\n".join(chunk), page, total_pages, len(filtered)

    def tail_lines(self, project_slug: str, *, stream: str = "stderr", line_count: int = 25, filter_mode: str = "all") -> str:
        path = self.stdout_path(project_slug) if stream == "stdout" else self.stderr_path(project_slug)
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        filtered = self._apply_filter(lines, filter_mode)
        return "\n".join(filtered[-line_count:])

    def _apply_filter(self, lines: list[str], filter_mode: str) -> list[str]:
        if filter_mode not in self.FILTER_KEYWORDS or filter_mode == "all":
            return lines
        keywords = self.FILTER_KEYWORDS[filter_mode]
        return [line for line in lines if any(token in line.lower() for token in keywords)]
