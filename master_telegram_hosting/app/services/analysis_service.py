from __future__ import annotations

import ast
import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.db.models.project import AnalysisGrade, RuntimeKind


@dataclass(slots=True)
class Issue:
    level: str
    file: str
    line: int | None
    snippet: str | None
    message: str


@dataclass(slots=True)
class AnalysisResult:
    grade: AnalysisGrade
    summary: str
    issues: list[Issue] = field(default_factory=list)

    def as_json(self) -> dict:
        return {
            "grade": self.grade.value,
            "summary": self.summary,
            "issues": [issue.__dict__ for issue in self.issues],
        }


class AnalysisService:
    PY_DANGER_PATTERNS = [
        (r"os\.system\(", "Shell execution detected"),
        (r"subprocess\.(run|Popen|call)\(", "Subprocess execution detected"),
        (r"shutil\.rmtree\(", "Recursive delete detected"),
        (r"rm\s+-rf", "Dangerous shell wipe pattern detected"),
        (r"while\s+True\s*:", "Infinite loop pattern detected"),
        (r"requests\.get\(.+http", "Network download pattern detected"),
    ]
    JS_DANGER_PATTERNS = [
        (r"child_process", "Child process module usage detected"),
        (r"exec\(", "Shell exec detected"),
        (r"spawn\(", "Process spawn detected"),
        (r"rm\s+-rf", "Dangerous shell wipe pattern detected"),
        (r"while\s*\(\s*true\s*\)", "Infinite loop pattern detected"),
        (r"fetch\(.+http", "Network fetch pattern detected"),
    ]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze_project(self, root: Path, runtime: RuntimeKind, entry_file: str) -> AnalysisResult:
        entry_path = root / entry_file
        if not entry_path.exists():
            return AnalysisResult(
                grade=AnalysisGrade.BLOCK,
                summary=f"Entrypoint `{entry_file}` was not found.",
                issues=[Issue("error", entry_file, None, None, "Missing entrypoint file")],
            )

        if runtime == RuntimeKind.PYTHON:
            return await self._analyze_python(entry_path)
        return await self._analyze_javascript(entry_path)

    async def _analyze_python(self, path: Path) -> AnalysisResult:
        issues: list[Issue] = []
        source = path.read_text(encoding="utf-8", errors="ignore")

        try:
            ast.parse(source)
        except SyntaxError as exc:
            return AnalysisResult(
                grade=AnalysisGrade.BLOCK,
                summary="Python syntax error detected.",
                issues=[Issue("error", path.name, exc.lineno, exc.text.strip() if exc.text else None, exc.msg)],
            )

        issues.extend(self._scan_patterns(source, path.name, self.PY_DANGER_PATTERNS))
        grade = self._derive_grade(issues)
        summary = self._summarize(grade, issues)
        return AnalysisResult(grade=grade, summary=summary, issues=issues)

    async def _analyze_javascript(self, path: Path) -> AnalysisResult:
        issues: list[Issue] = []
        source = path.read_text(encoding="utf-8", errors="ignore")

        proc = await asyncio.create_subprocess_exec(
            self.settings.node_executable,
            "--check",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return AnalysisResult(
                grade=AnalysisGrade.BLOCK,
                summary="JavaScript syntax error detected.",
                issues=[Issue("error", path.name, None, None, stderr.decode().strip())],
            )

        issues.extend(self._scan_patterns(source, path.name, self.JS_DANGER_PATTERNS))
        grade = self._derive_grade(issues)
        summary = self._summarize(grade, issues)
        return AnalysisResult(grade=grade, summary=summary, issues=issues)

    def file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _scan_patterns(self, source: str, file_name: str, patterns: list[tuple[str, str]]) -> list[Issue]:
        issues: list[Issue] = []
        for pattern, message in patterns:
            for match in re.finditer(pattern, source, flags=re.IGNORECASE | re.MULTILINE):
                line = source[: match.start()].count("\n") + 1
                snippet = source.splitlines()[line - 1][:240] if source.splitlines() else None
                issues.append(Issue("warning", file_name, line, snippet, message))
        return issues

    def _derive_grade(self, issues: list[Issue]) -> AnalysisGrade:
        if not issues:
            return AnalysisGrade.PASS
        if len(issues) >= 4:
            return AnalysisGrade.BLOCK
        return AnalysisGrade.WARN

    def _summarize(self, grade: AnalysisGrade, issues: list[Issue]) -> str:
        if grade == AnalysisGrade.PASS:
            return "Your code looks fine for now. Waiting for admin approval."
        if grade == AnalysisGrade.WARN:
            return f"Analysis found {len(issues)} warning(s). Admin review is required before any run."
        return f"Analysis blocked this upload with {len(issues)} high-risk finding(s)."
