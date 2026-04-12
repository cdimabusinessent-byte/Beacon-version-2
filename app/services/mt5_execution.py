from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ExecutionRequest, Mt5ExecutionJob, Mt5Profile, Mt5Worker, Trade
from app.services.mt5 import Mt5TradingClient
from app.services.mt5_profiles import Mt5ProfileService


class Mt5ExecutionService:
    def __init__(self, settings: Settings, mt5_client: Any | None = None, mt5_client_factory: type[Mt5TradingClient] = Mt5TradingClient):
        self.settings = settings
        self.mt5 = mt5_client or mt5_client_factory(settings)
        self.mt5_client_factory = mt5_client_factory

    @property
    def execution_mode(self) -> str:
        return self.settings.effective_mt5_execution_mode

    async def place_market_buy(
        self,
        db: Session,
        symbol: str,
        volume: Decimal,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
        owner_id: str | None = None,
        signal_symbol: str | None = None,
        execution_request_id: int | None = None,
    ) -> dict[str, Any]:
        if self.execution_mode == "direct":
            result = await self.mt5.place_market_buy(
                symbol,
                volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
                client_order_id=client_order_id,
            )
            return {**result, "execution_mode": "direct", "is_queued": False}
        return self._queue_job(
            db,
            owner_id=owner_id,
            signal_symbol=signal_symbol,
            execution_symbol=symbol,
            action="BUY",
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
            execution_request_id=execution_request_id,
        )

    async def place_market_sell(
        self,
        db: Session,
        symbol: str,
        volume: Decimal,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
        owner_id: str | None = None,
        signal_symbol: str | None = None,
        execution_request_id: int | None = None,
    ) -> dict[str, Any]:
        if self.execution_mode == "direct":
            result = await self.mt5.place_market_sell(
                symbol,
                volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
                client_order_id=client_order_id,
            )
            return {**result, "execution_mode": "direct", "is_queued": False}
        return self._queue_job(
            db,
            owner_id=owner_id,
            signal_symbol=signal_symbol,
            execution_symbol=symbol,
            action="SELL",
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
            execution_request_id=execution_request_id,
        )

    def _queue_job(
        self,
        db: Session,
        owner_id: str | None,
        signal_symbol: str | None,
        execution_symbol: str,
        action: str,
        volume: Decimal,
        stop_loss: float | None,
        take_profit: float | None,
        client_order_id: str | None,
        execution_request_id: int | None,
    ) -> dict[str, Any]:
        resolved_owner_id = (owner_id or self.settings.mt5_runtime_owner_id).strip() or "local"
        active_profile_id = self._resolve_active_profile_id(db, resolved_owner_id)
        assigned_worker_key = self._resolve_assigned_worker_key(db, resolved_owner_id, active_profile_id)
        job = Mt5ExecutionJob(
            owner_id=resolved_owner_id,
            profile_id=active_profile_id,
            assigned_worker_key=assigned_worker_key,
            execution_request_id=execution_request_id,
            client_order_id=(client_order_id or "").strip() or f"queued-{action.lower()}-{datetime.now(UTC).timestamp()}",
            signal_symbol=(signal_symbol or "").strip() or None,
            execution_symbol=execution_symbol,
            action=action,
            volume=float(volume),
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="QUEUED",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return {
            "job_id": job.id,
            "order": self.queue_order_reference(job.id),
            "volume": float(job.volume),
            "status": "QUEUED",
            "execution_mode": "queue",
            "is_queued": True,
            "profile_id": job.profile_id,
            "assigned_worker_key": job.assigned_worker_key,
        }

    def _resolve_active_profile_id(self, db: Session, owner_id: str) -> int | None:
        statement = (
            select(Mt5Profile.id)
            .where(Mt5Profile.owner_id == owner_id, Mt5Profile.is_active.is_(True))
            .order_by(Mt5Profile.updated_at.desc(), Mt5Profile.id.desc())
            .limit(1)
        )
        return db.scalar(statement)

    def _resolve_assigned_worker_key(self, db: Session, owner_id: str, profile_id: int | None) -> str | None:
        statement = select(Mt5Worker).where(Mt5Worker.owner_id == owner_id)
        if profile_id is None:
            statement = statement.where(Mt5Worker.profile_id.is_(None))
        else:
            statement = statement.where(Mt5Worker.profile_id == profile_id)
        workers = db.scalars(statement.order_by(Mt5Worker.updated_at.desc(), Mt5Worker.id.desc())).all()
        if not workers:
            return None

        online_worker = next((item for item in workers if item.status == "ONLINE"), None)
        if online_worker is not None:
            return online_worker.worker_key

        provisioned_worker = next((item for item in workers if item.status in {"PROVISIONED", "OFFLINE", "ERROR"}), None)
        if provisioned_worker is not None:
            return provisioned_worker.worker_key
        return workers[0].worker_key

    def queue_order_reference(self, job_id: int) -> str:
        return f"queue-job-{job_id}"

    def claim_next_job(
        self,
        db: Session,
        owner_id: str | None = None,
        profile_id: int | None = None,
        worker_key: str | None = None,
    ) -> Mt5ExecutionJob | None:
        self.release_stale_claims(db)
        statement = select(Mt5ExecutionJob).where(Mt5ExecutionJob.status == "QUEUED")
        if owner_id:
            statement = statement.where(Mt5ExecutionJob.owner_id == owner_id)
        if profile_id is None:
            statement = statement.where(Mt5ExecutionJob.profile_id.is_(None))
        else:
            statement = statement.where(Mt5ExecutionJob.profile_id == profile_id)
        if worker_key:
            statement = statement.where(
                or_(
                    Mt5ExecutionJob.assigned_worker_key.is_(None),
                    Mt5ExecutionJob.assigned_worker_key == worker_key,
                )
            )
        statement = statement.order_by(Mt5ExecutionJob.submitted_at.asc(), Mt5ExecutionJob.id.asc()).limit(1)
        job = db.scalar(statement)
        if job is None:
            return None
        job.status = "CLAIMED"
        job.claimed_at = datetime.now(UTC)
        job.claimed_by_worker_key = worker_key
        db.add(job)
        db.commit()
        db.refresh(job)
        if worker_key:
            self.touch_worker(db, worker_key, last_claimed=True)
        return job

    def release_stale_claims(self, db: Session) -> int:
        timeout = max(30, int(self.settings.mt5_worker_claim_timeout_seconds))
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
        rows = db.scalars(
            select(Mt5ExecutionJob).where(
                Mt5ExecutionJob.status == "CLAIMED",
                Mt5ExecutionJob.claimed_at.is_not(None),
            )
        ).all()
        stale = []
        for row in rows:
            claimed_at = row.claimed_at
            if claimed_at is not None and claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=UTC)
            if claimed_at is not None and claimed_at <= cutoff:
                stale.append(row)
        for row in stale:
            row.status = "QUEUED"
            row.claimed_at = None
            row.claimed_by_worker_key = None
            db.add(row)
        if stale:
            db.commit()
        return len(stale)

    def register_worker(
        self,
        db: Session,
        worker_key: str,
        owner_id: str,
        profile_id: int | None = None,
        label: str | None = None,
        terminal_path: str | None = None,
        status: str = "ONLINE",
        error: str | None = None,
    ) -> Mt5Worker:
        statement = select(Mt5Worker).where(Mt5Worker.worker_key == worker_key).limit(1)
        worker = db.scalar(statement)
        now = datetime.now(UTC)
        if worker is None:
            worker = Mt5Worker(worker_key=worker_key, created_at=now)
        worker.owner_id = owner_id
        worker.profile_id = profile_id
        worker.label = (label or "").strip() or None
        worker.terminal_path = (terminal_path or "").strip() or None
        worker.status = status
        worker.last_error = None if error is None else error[:255]
        worker.heartbeat_at = now
        worker.updated_at = now
        db.add(worker)
        db.commit()
        db.refresh(worker)
        return worker

    def touch_worker(self, db: Session, worker_key: str, last_claimed: bool = False, error: str | None = None) -> Mt5Worker | None:
        worker = db.scalar(select(Mt5Worker).where(Mt5Worker.worker_key == worker_key).limit(1))
        if worker is None:
            return None
        now = datetime.now(UTC)
        worker.heartbeat_at = now
        worker.updated_at = now
        if last_claimed:
            worker.last_claimed_at = now
        if error is not None:
            worker.last_error = error[:255]
            worker.status = "ERROR"
        else:
            worker.status = "ONLINE"
            worker.last_error = None
        db.add(worker)
        db.commit()
        db.refresh(worker)
        return worker

    async def process_next_job(
        self,
        db: Session,
        runtime_settings: Settings,
        profile_service: Mt5ProfileService | None = None,
        owner_id: str | None = None,
        profile_id: int | None = None,
        worker_key: str | None = None,
    ) -> dict[str, Any] | None:
        job = self.claim_next_job(
            db,
            owner_id=owner_id or runtime_settings.mt5_runtime_owner_id,
            profile_id=profile_id,
            worker_key=worker_key,
        )
        if job is None:
            return None

        effective_owner = (owner_id or job.owner_id or runtime_settings.mt5_runtime_owner_id).strip() or "local"
        if profile_service is not None:
            if job.profile_id is not None and hasattr(profile_service, "apply_profile_if_valid"):
                applied_profile = await profile_service.apply_profile_if_valid(
                    db,
                    job.profile_id,
                    runtime_settings=runtime_settings,
                    owner_id=effective_owner,
                )
                if applied_profile is None:
                    error = "Queued MT5 execution blocked: assigned MT5 profile failed validation."
                    self.fail_job(db, job, error)
                    return {"status": "FAILED", "error": error, "client_order_id": job.client_order_id}
            else:
                active_profile = profile_service.get_active_profile(db, owner_id=effective_owner)
                if active_profile is not None:
                    applied_profile = await profile_service.apply_saved_runtime_profile_if_valid(
                        db,
                        runtime_settings=runtime_settings,
                        owner_id=effective_owner,
                    )
                    if applied_profile is None:
                        error = "Queued MT5 execution blocked: active runtime profile failed validation."
                        self.fail_job(db, job, error)
                        return {"status": "FAILED", "error": error, "client_order_id": job.client_order_id}

        client = self.mt5_client_factory(runtime_settings)
        try:
            volume = Decimal(str(job.volume))
            if job.action == "BUY":
                result = await client.place_market_buy(
                    job.execution_symbol,
                    volume,
                    stop_loss=job.stop_loss,
                    take_profit=job.take_profit,
                    client_order_id=job.client_order_id,
                )
            else:
                result = await client.place_market_sell(
                    job.execution_symbol,
                    volume,
                    stop_loss=job.stop_loss,
                    take_profit=job.take_profit,
                    client_order_id=job.client_order_id,
                )
            self.complete_job(db, job, result)
            if worker_key:
                self.touch_worker(db, worker_key)
            return result
        except Exception as exc:
            if worker_key:
                self.touch_worker(db, worker_key, error=str(exc))
            self.fail_job(db, job, str(exc))
            raise

    def complete_job(self, db: Session, job: Mt5ExecutionJob, result: dict[str, Any]) -> None:
        job.status = "FILLED"
        job.result_payload = json.dumps(result, default=str)
        job.error = None
        job.completed_at = datetime.now(UTC)
        db.add(job)
        self._sync_execution_records(db, job, result=result, failed=False)
        db.commit()
        db.refresh(job)

    def fail_job(self, db: Session, job: Mt5ExecutionJob, error: str) -> None:
        job.status = "FAILED"
        job.error = error[:255]
        job.completed_at = datetime.now(UTC)
        db.add(job)
        self._sync_execution_records(db, job, result=None, failed=True)
        db.commit()
        db.refresh(job)

    def _sync_execution_records(self, db: Session, job: Mt5ExecutionJob, result: dict[str, Any] | None, failed: bool) -> None:
        if job.execution_request_id is not None:
            request = db.get(ExecutionRequest, job.execution_request_id)
            if request is not None:
                request.status = "FAILED" if failed else "FILLED"
                request.error = job.error
                request.broker_order_id = None if failed else str((result or {}).get("order") or (result or {}).get("deal") or job.id)
                request.completed_at = datetime.now(UTC)
                db.add(request)

        trade = db.scalar(select(Trade).where(Trade.exchange_order_id == self.queue_order_reference(job.id)).limit(1))
        if trade is None:
            return
        if failed:
            trade.status = "FAILED"
            trade.reconciliation_status = "FAILED"
            trade.notes = f"{trade.notes or ''} | queue_failed={job.error}".strip(" |")
            db.add(trade)
            return

        fill_price = float((result or {}).get("price") or trade.intended_price or trade.price)
        executed_quantity = float((result or {}).get("volume") or trade.quantity)
        trade.status = "FILLED"
        trade.price = fill_price
        trade.fill_price = fill_price
        trade.quantity = executed_quantity
        trade.quote_amount = fill_price * executed_quantity
        trade.exchange_order_id = str((result or {}).get("order") or (result or {}).get("deal") or self.queue_order_reference(job.id))
        trade.broker_position_id = trade.exchange_order_id
        trade.reconciliation_status = "PENDING"
        db.add(trade)