from __future__ import annotations

import json

import redis.asyncio as redis

from app.config import Settings


class StateStore:
    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def set_state(self, telegram_user_id: int, payload: dict) -> None:
        await self.client.setex(
            f"state:{telegram_user_id}",
            self.settings.state_ttl_seconds,
            json.dumps(payload),
        )

    async def set_upload_wait(self, telegram_user_id: int) -> None:
        await self.set_state(telegram_user_id, {"kind": "awaiting_upload"})

    async def set_editor_replace_wait(self, telegram_user_id: int, *, project_id: int, relative_path: str, file_id: int) -> None:
        await self.set_state(
            telegram_user_id,
            {
                "kind": "awaiting_editor_replace",
                "project_id": project_id,
                "relative_path": relative_path,
                "file_id": file_id,
            },
        )

    async def set_editor_patch_wait(self, telegram_user_id: int, *, project_id: int, relative_path: str, file_id: int) -> None:
        await self.set_state(
            telegram_user_id,
            {
                "kind": "awaiting_editor_patch",
                "project_id": project_id,
                "relative_path": relative_path,
                "file_id": file_id,
            },
        )

    async def set_patch_preview(
        self,
        telegram_user_id: int,
        *,
        project_id: int,
        relative_path: str,
        file_id: int,
        start_line: int,
        end_line: int,
        replacement_text: str,
        diff_preview: str,
    ) -> None:
        await self.set_state(
            telegram_user_id,
            {
                "kind": "awaiting_patch_confirm",
                "project_id": project_id,
                "relative_path": relative_path,
                "file_id": file_id,
                "start_line": start_line,
                "end_line": end_line,
                "replacement_text": replacement_text,
                "diff_preview": diff_preview,
            },
        )

    async def set_packages_wait(self, telegram_user_id: int, project_id: int, runtime: str) -> None:
        await self.set_state(
            telegram_user_id,
            {
                "kind": "awaiting_packages",
                "project_id": project_id,
                "runtime": runtime,
            },
        )

    async def get_state(self, telegram_user_id: int) -> dict | None:
        raw = await self.client.get(f"state:{telegram_user_id}")
        return json.loads(raw) if raw else None

    async def clear(self, telegram_user_id: int) -> None:
        await self.client.delete(f"state:{telegram_user_id}")
