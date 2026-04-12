from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Mt5Profile, Mt5Worker


class Mt5WorkerService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def list_workers(self, db: Session, owner_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(Mt5Worker)
        if owner_id:
            statement = statement.where(Mt5Worker.owner_id == owner_id)
        statement = statement.order_by(Mt5Worker.owner_id.asc(), Mt5Worker.updated_at.desc(), Mt5Worker.id.desc())
        workers = db.scalars(statement).all()
        profile_ids = {worker.profile_id for worker in workers if worker.profile_id is not None}
        profiles = {}
        if profile_ids:
            items = db.scalars(select(Mt5Profile).where(Mt5Profile.id.in_(profile_ids))).all()
            profiles = {item.id: item for item in items}
        return [self._serialize_worker(item, profiles.get(item.profile_id)) for item in workers]

    def provision_worker(
        self,
        db: Session,
        worker_key: str,
        owner_id: str,
        profile_id: int | None = None,
        label: str | None = None,
        terminal_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_key = (worker_key or "").strip()
        if not normalized_key:
            raise ValueError("worker_key is required.")

        profile = self._validate_profile(db, owner_id, profile_id)
        worker = db.scalar(select(Mt5Worker).where(Mt5Worker.worker_key == normalized_key).limit(1))
        now = datetime.now(UTC)
        if worker is None:
            worker = Mt5Worker(worker_key=normalized_key, created_at=now)
        elif worker.owner_id and worker.owner_id != owner_id:
            raise ValueError("Worker key is already assigned to a different owner.")

        worker.owner_id = owner_id
        worker.profile_id = profile.id if profile is not None else None
        worker.label = (label or "").strip() or worker.label or normalized_key
        worker.terminal_path = (terminal_path or "").strip() or worker.terminal_path
        if worker.status not in {"ONLINE", "ERROR"}:
            worker.status = "PROVISIONED"
        worker.updated_at = now
        db.add(worker)
        db.commit()
        db.refresh(worker)
        return self._serialize_worker(worker, profile)

    def assign_worker(
        self,
        db: Session,
        worker_key: str,
        owner_id: str,
        profile_id: int | None = None,
        label: str | None = None,
        terminal_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_key = (worker_key or "").strip()
        if not normalized_key:
            raise ValueError("worker_key is required.")

        worker = db.scalar(select(Mt5Worker).where(Mt5Worker.worker_key == normalized_key).limit(1))
        if worker is None:
            raise ValueError("MT5 worker not found.")
        if worker.owner_id != owner_id:
            raise ValueError("MT5 worker does not belong to the requested owner.")

        profile = self._validate_profile(db, owner_id, profile_id)
        worker.profile_id = profile.id if profile is not None else None
        if label is not None:
            worker.label = label.strip() or worker.label
        if terminal_path is not None:
            worker.terminal_path = terminal_path.strip() or None
        if worker.status not in {"ONLINE", "ERROR"}:
            worker.status = "PROVISIONED"
        worker.updated_at = datetime.now(UTC)
        db.add(worker)
        db.commit()
        db.refresh(worker)
        return self._serialize_worker(worker, profile)

    def auto_assign_owner_worker(self, db: Session, owner_id: str, profile_id: int | None) -> dict[str, Any] | None:
        profile = self._validate_profile(db, owner_id, profile_id)
        if profile is None:
            return None

        workers = db.scalars(
            select(Mt5Worker)
            .where(Mt5Worker.owner_id == owner_id)
            .order_by(Mt5Worker.updated_at.desc(), Mt5Worker.id.desc())
        ).all()
        if not workers:
            return None

        existing = next((item for item in workers if item.profile_id == profile.id), None)
        if existing is not None:
            return self._serialize_worker(existing, profile)

        unassigned = [item for item in workers if item.profile_id is None]
        if len(unassigned) != 1:
            return None

        worker = unassigned[0]
        worker.profile_id = profile.id
        if worker.status not in {"ONLINE", "ERROR"}:
            worker.status = "PROVISIONED"
        worker.updated_at = datetime.now(UTC)
        db.add(worker)
        db.commit()
        db.refresh(worker)
        return self._serialize_worker(worker, profile)

    def _validate_profile(self, db: Session, owner_id: str, profile_id: int | None) -> Mt5Profile | None:
        if profile_id is None:
            return None
        profile = db.get(Mt5Profile, profile_id)
        if profile is None:
            raise ValueError("MT5 profile not found.")
        if profile.owner_id != owner_id:
            raise ValueError("MT5 profile does not belong to the requested owner.")
        return profile

    def _serialize_worker(self, worker: Mt5Worker, profile: Mt5Profile | None = None) -> dict[str, Any]:
        return {
            "id": worker.id,
            "worker_key": worker.worker_key,
            "owner_id": worker.owner_id,
            "profile_id": worker.profile_id,
            "profile_label": None if profile is None else profile.label,
            "label": worker.label,
            "terminal_path": worker.terminal_path,
            "status": worker.status,
            "last_error": worker.last_error,
            "heartbeat_at": None if worker.heartbeat_at is None else worker.heartbeat_at.isoformat(),
            "last_claimed_at": None if worker.last_claimed_at is None else worker.last_claimed_at.isoformat(),
            "created_at": worker.created_at.isoformat(),
            "updated_at": worker.updated_at.isoformat(),
        }
