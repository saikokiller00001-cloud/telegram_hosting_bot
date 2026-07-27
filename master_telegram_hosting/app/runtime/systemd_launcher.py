from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings


@dataclass(slots=True)
class RuntimeSecurityProfile:
    cpu_quota_pct: int
    memory_max_mb: int
    runtime_max_sec: int
    private_tmp: bool = True
    protect_system: str = "full"
    protect_home: str = "read-only"
    no_new_privileges: bool = True
    restrict_suid_sgid: bool = True
    lock_personality: bool = True


class SystemdLauncher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def unit_name(self, project_id: int) -> str:
        return f"{self.settings.systemd_unit_prefix}-{project_id}.service"

    async def start_unit(
        self,
        *,
        project_id: int,
        command: list[str],
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        profile: RuntimeSecurityProfile,
        env: dict[str, str] | None = None,
    ) -> dict:
        unit_name = self.unit_name(project_id)
        await self.reset_failed(unit_name)

        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)

        bash_cmd = (
            f"umask 077; exec {shlex.join(command)} "
            f">> {shlex.quote(str(stdout_path))} 2>> {shlex.quote(str(stderr_path))}"
        )

        cmd = self._prefix(self.settings.systemd_run_command) + [
            "--unit",
            unit_name,
            "--collect",
            "--service-type=simple",
            f"--working-directory={cwd}",
        ]

        for prop in self._build_properties(profile, cwd, stdout_path.parent):
            cmd.append(f"--property={prop}")

        for key, value in (env or {}).items():
            cmd.append(f"--setenv={key}={value}")

        cmd += ["/bin/bash", "-lc", bash_cmd]
        stdout, stderr, return_code = await self._run_capture(cmd)
        if return_code != 0:
            raise RuntimeError(f"systemd-run failed: {(stderr or stdout).strip() or 'unknown error'}")
        status = await self.show_unit(unit_name)
        status["UnitName"] = unit_name
        return status

    async def stop_unit(self, unit_name: str) -> dict:
        cmd = self._prefix(self.settings.systemctl_command) + ["stop", unit_name]
        await self._run_capture(cmd)
        return await self.show_unit(unit_name)

    async def reset_failed(self, unit_name: str) -> None:
        cmd = self._prefix(self.settings.systemctl_command) + ["reset-failed", unit_name]
        await self._run_capture(cmd)

    async def show_unit(self, unit_name: str) -> dict:
        cmd = self._prefix(self.settings.systemctl_command) + [
            "show",
            unit_name,
            "--no-page",
            "--property=Id,ActiveState,SubState,MainPID,ExecMainStatus,Result",
        ]
        stdout, stderr, return_code = await self._run_capture(cmd)
        if return_code != 0 and "not-found" in (stdout + stderr).lower():
            return {
                "Id": unit_name,
                "ActiveState": "inactive",
                "SubState": "dead",
                "MainPID": "0",
                "ExecMainStatus": "0",
                "Result": "not-found",
            }
        data: dict[str, str] = {}
        for line in stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value
        if not data:
            data = {
                "Id": unit_name,
                "ActiveState": "inactive",
                "SubState": "dead",
                "MainPID": "0",
                "ExecMainStatus": "0",
                "Result": "unknown",
            }
        return data

    def is_active(self, status: dict) -> bool:
        return status.get("ActiveState") in {"active", "activating", "reloading"}

    def _build_properties(self, profile: RuntimeSecurityProfile, cwd: Path, logs_dir: Path) -> list[str]:
        props = [
            "KillMode=control-group",
            "UMask=0077",
            f"ReadWritePaths={cwd} {logs_dir}",
            f"ProtectSystem={profile.protect_system}",
            f"ProtectHome={profile.protect_home}",
            f"PrivateTmp={'yes' if profile.private_tmp else 'no'}",
            f"NoNewPrivileges={'yes' if profile.no_new_privileges else 'no'}",
            f"RestrictSUIDSGID={'yes' if profile.restrict_suid_sgid else 'no'}",
            f"LockPersonality={'yes' if profile.lock_personality else 'no'}",
        ]
        if profile.memory_max_mb > 0:
            props.append(f"MemoryMax={profile.memory_max_mb}M")
        if profile.cpu_quota_pct > 0:
            props.append(f"CPUQuota={profile.cpu_quota_pct}%")
        if profile.runtime_max_sec > 0:
            props.append(f"RuntimeMaxSec={profile.runtime_max_sec}")
        return props

    def _prefix(self, binary: str) -> list[str]:
        if self.settings.systemd_use_sudo:
            return ["sudo", "-n", binary]
        return [binary]

    async def _run_capture(self, cmd: list[str]) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode(), stderr.decode(), proc.returncode
