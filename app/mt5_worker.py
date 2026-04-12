from __future__ import annotations

import asyncio

from app.config import get_settings
from app.database import SessionLocal, ensure_database_schema
from app.services.mt5_execution import Mt5ExecutionService
from app.services.mt5_profiles import Mt5ProfileService


async def run_worker_loop() -> None:
    settings = get_settings()
    ensure_database_schema()
    execution_service = Mt5ExecutionService(settings)
    profile_service = Mt5ProfileService(settings)
    poll_seconds = max(1, int(settings.mt5_worker_poll_seconds))
    worker_key = settings.mt5_worker_key.strip() or "local-worker"
    worker_label = settings.mt5_worker_label.strip() or worker_key
    owner_id = (settings.mt5_runtime_owner_id or "local").strip() or "local"

    while True:
        db = SessionLocal()
        try:
            active_profile = profile_service.get_active_profile(db, owner_id=owner_id)
            active_profile_id = getattr(active_profile, "id", None)
            execution_service.register_worker(
                db,
                worker_key=worker_key,
                owner_id=owner_id,
                profile_id=active_profile_id,
                label=worker_label,
                terminal_path=settings.mt5_terminal_path,
            )
            await execution_service.process_next_job(
                db,
                runtime_settings=settings,
                profile_service=profile_service,
                owner_id=owner_id,
                profile_id=active_profile_id,
                worker_key=worker_key,
            )
        finally:
            db.close()
        await asyncio.sleep(poll_seconds)


def main() -> None:
    asyncio.run(run_worker_loop())


if __name__ == "__main__":
    main()