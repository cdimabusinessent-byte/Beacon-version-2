from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Base, Mt5ExecutionJob, Mt5Profile, Mt5Worker
from app.services.mt5_execution import Mt5ExecutionService


class RecordingMt5Client:
    calls: list[dict[str, object]] = []

    def __init__(self, runtime_settings: Settings):
        self.runtime_settings = runtime_settings

    async def place_market_buy(
        self,
        symbol: str,
        volume: Decimal,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        payload = {
            "owner_id": self.runtime_settings.mt5_runtime_owner_id,
            "login": self.runtime_settings.mt5_login,
            "server": self.runtime_settings.mt5_server,
            "symbol": symbol,
            "volume": float(volume),
            "client_order_id": client_order_id,
        }
        self.__class__.calls.append(payload)
        return {"order": f"filled-{client_order_id}", "price": 1.2345, "volume": float(volume)}

    async def place_market_sell(self, *args, **kwargs):
        raise AssertionError("SELL is not used in this smoke test")


class ProfileBinder:
    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory

    def get_active_profile(self, db, owner_id: str | None = None):
        statement = select(Mt5Profile).where(Mt5Profile.is_active.is_(True))
        if owner_id:
            statement = statement.where(Mt5Profile.owner_id == owner_id)
        return db.scalar(statement.limit(1))

    async def apply_profile_if_valid(self, db, profile_id: int, runtime_settings=None, trading_service=None, owner_id: str | None = None):
        profile = db.get(Mt5Profile, profile_id)
        if profile is None:
            return None
        runtime_settings.mt5_runtime_owner_id = profile.owner_id
        runtime_settings.mt5_login = profile.login
        runtime_settings.mt5_server = profile.server
        runtime_settings.mt5_symbol = "GBPUSDm" if profile.owner_id == "trader-alpha" else "EURUSDm"
        runtime_settings.mt5_symbols = runtime_settings.mt5_symbol
        return {"id": profile.id, "owner_id": profile.owner_id}


def seed_owner(db, owner_id: str, profile_id: int, worker_key: str, login: int, server: str) -> None:
    now = datetime.now(UTC)
    db.add(
        Mt5Profile(
            id=profile_id,
            owner_id=owner_id,
            label=f"{owner_id}-profile",
            login=login,
            password_encrypted="encrypted",
            server=server,
            terminal_path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
            symbols_csv="GBPUSDm,EURUSDm",
            volume_lots=0.01,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        Mt5Worker(
            worker_key=worker_key,
            owner_id=owner_id,
            profile_id=profile_id,
            label=f"{owner_id}-worker",
            terminal_path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
            status="PROVISIONED",
            created_at=now,
            updated_at=now,
        )
    )


def main() -> None:
    RecordingMt5Client.calls = []
    engine = None
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "managed_worker_smoke.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        db = session_factory()
        try:
            seed_owner(db, "trader-alpha", 1, "worker-alpha-1", 111111, "Broker-Alpha")
            seed_owner(db, "trader-beta", 2, "worker-beta-1", 222222, "Broker-Beta")
            db.commit()

            settings = Settings(
                database_url=f"sqlite:///{db_path}",
                execution_provider="mt5",
                mt5_execution_mode="queue",
                mt5_runtime_owner_id="local",
            )
            execution_service = Mt5ExecutionService(settings, mt5_client_factory=RecordingMt5Client)

            alpha_job = execution_service._queue_job(
                db,
                owner_id="trader-alpha",
                signal_symbol="GBPUSDm",
                execution_symbol="GBPUSDm",
                action="BUY",
                volume=Decimal("0.01"),
                stop_loss=None,
                take_profit=None,
                client_order_id="alpha-job-1",
                execution_request_id=None,
            )
            beta_job = execution_service._queue_job(
                db,
                owner_id="trader-beta",
                signal_symbol="EURUSDm",
                execution_symbol="EURUSDm",
                action="BUY",
                volume=Decimal("0.02"),
                stop_loss=None,
                take_profit=None,
                client_order_id="beta-job-1",
                execution_request_id=None,
            )

            profile_service = ProfileBinder(session_factory)
            alpha_settings = settings.model_copy(update={"mt5_runtime_owner_id": "trader-alpha"})
            beta_settings = settings.model_copy(update={"mt5_runtime_owner_id": "trader-beta"})

            import asyncio

            asyncio.run(
                execution_service.process_next_job(
                    db,
                    runtime_settings=alpha_settings,
                    profile_service=profile_service,
                    owner_id="trader-alpha",
                    profile_id=alpha_job["profile_id"],
                    worker_key="worker-alpha-1",
                )
            )
            asyncio.run(
                execution_service.process_next_job(
                    db,
                    runtime_settings=beta_settings,
                    profile_service=profile_service,
                    owner_id="trader-beta",
                    profile_id=beta_job["profile_id"],
                    worker_key="worker-beta-1",
                )
            )

            jobs = db.scalars(select(Mt5ExecutionJob).order_by(Mt5ExecutionJob.id.asc())).all()
            if len(jobs) != 2 or any(job.status != "FILLED" for job in jobs):
                raise SystemExit("Smoke test failed: expected two FILLED jobs.")
            if {call["owner_id"] for call in RecordingMt5Client.calls} != {"trader-alpha", "trader-beta"}:
                raise SystemExit("Smoke test failed: worker calls were not isolated by owner.")

            print("Managed worker smoke test passed.")
            for call in RecordingMt5Client.calls:
                print(
                    f"owner={call['owner_id']} login={call['login']} server={call['server']} "
                    f"symbol={call['symbol']} client_order_id={call['client_order_id']}"
                )
        finally:
            db.close()
            if engine is not None:
                engine.dispose()


if __name__ == "__main__":
    main()