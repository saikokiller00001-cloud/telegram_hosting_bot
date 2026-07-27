from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system import SystemEvent


class AuditService:
    async def record(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        summary: str,
        severity: str = "info",
        actor_user_id: int | None = None,
        target_user_id: int | None = None,
        project_id: int | None = None,
        run_instance_id: int | None = None,
        payload: dict | None = None,
    ) -> SystemEvent:
        event = SystemEvent(
            event_type=event_type,
            summary=summary[:255],
            severity=severity,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            project_id=project_id,
            run_instance_id=run_instance_id,
            payload_json=payload,
        )
        session.add(event)
        await session.flush()
        return event
