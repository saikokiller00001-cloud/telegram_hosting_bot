from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from app.config import Settings
from app.runtime.systemd_launcher import RuntimeSecurityProfile, SystemdLauncher

ExitCallback = Callable[[int, Path, Path, bool, str], Awaitable[None]]


@dataclass(slots=True)
class ManagedUnit:
    project_id: int
    unit_name: str
    stdout_path: Path
    stderr_path: Path
    stop_requested: bool = False
    watcher: asyncio.Task | None = None


class ProcessSupervisor:
    def __init__(self, settings: Settings, launcher: SystemdLauncher) -> None:
        self.settings = settings
        self.launcher = launcher
        self._managed: dict[int, ManagedUnit] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        project_id: int,
        command: list[str],
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        profile: RuntimeSecurityProfile,
        on_exit: ExitCallback,
        env: dict[str, str] | None = None,
    ) -> dict:
        async with self._lock:
            status = await self.status(project_id)
            if self.launcher.is_active(status):
                raise ValueError("Project is already running.")

            result = await self.launcher.start_unit(
                project_id=project_id,
                command=command,
                cwd=cwd,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                profile=profile,
                env=env,
            )
            unit_name = result.get("UnitName") or self.launcher.unit_name(project_id)
            managed = ManagedUnit(
                project_id=project_id,
                unit_name=unit_name,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            managed.watcher = asyncio.create_task(
                self._watch_unit(managed, on_exit),
                name=f"watch-unit-{project_id}",
            )
            self._managed[project_id] = managed
            return result

    async def adopt_existing(
        self,
        *,
        project_id: int,
        stdout_path: Path,
        stderr_path: Path,
        on_exit: ExitCallback,
    ) -> bool:
        async with self._lock:
            if project_id in self._managed:
                return True
            status = await self.status(project_id)
            if not self.launcher.is_active(status):
                return False
            unit_name = status.get("Id") or self.launcher.unit_name(project_id)
            managed = ManagedUnit(
                project_id=project_id,
                unit_name=unit_name,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            managed.watcher = asyncio.create_task(
                self._watch_unit(managed, on_exit),
                name=f"watch-adopted-unit-{project_id}",
            )
            self._managed[project_id] = managed
            return True

    async def stop(self, project_id: int) -> int | None:
        async with self._lock:
            unit_name = self.launcher.unit_name(project_id)
            managed = self._managed.get(project_id)
            if managed:
                managed.stop_requested = True
            else:
                self._managed[project_id] = ManagedUnit(
                    project_id=project_id,
                    unit_name=unit_name,
                    stdout_path=Path("/dev/null"),
                    stderr_path=Path("/dev/null"),
                    stop_requested=True,
                )
            await self.launcher.stop_unit(unit_name)

        deadline = self.settings.runtime_stop_grace_seconds + 3
        for _ in range(deadline):
            status = await self.status(project_id)
            if not self.launcher.is_active(status):
                return int(status.get("ExecMainStatus", "0") or 0)
            await asyncio.sleep(1)
        return None

    async def status(self, project_id: int) -> dict:
        return await self.launcher.show_unit(self.launcher.unit_name(project_id))

    async def is_running(self, project_id: int) -> bool:
        return self.launcher.is_active(await self.status(project_id))

    async def get_pid(self, project_id: int) -> int | None:
        status = await self.status(project_id)
        try:
            pid = int(status.get("MainPID", "0") or 0)
        except ValueError:
            return None
        return pid or None

    async def _watch_unit(self, managed: ManagedUnit, on_exit: ExitCallback) -> None:
        while True:
            status = await self.launcher.show_unit(managed.unit_name)
            if not self.launcher.is_active(status):
                exit_code = int(status.get("ExecMainStatus", "0") or 0)
                stop_requested = managed.stop_requested
                await on_exit(exit_code, managed.stdout_path, managed.stderr_path, stop_requested, managed.unit_name)
                async with self._lock:
                    current = self._managed.get(managed.project_id)
                    if current is managed:
                        self._managed.pop(managed.project_id, None)
                return
            await asyncio.sleep(self.settings.systemd_poll_interval_seconds)
