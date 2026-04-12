from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Mt5Profile
from app.schemas import Mt5ProfileCreateRequest
from app.services.mt5 import Mt5TradingClient


class Mt5ProfileService:
    def __init__(self, settings: Settings, mt5_client_factory: type[Mt5TradingClient] = Mt5TradingClient):
        self.settings = settings
        self.mt5_client_factory = mt5_client_factory

    @property
    def encryption_ready(self) -> bool:
        try:
            self._get_fernet()
            return True
        except ValueError:
            return False

    def list_profiles(self, db: Session, owner_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(Mt5Profile)
        if owner_id:
            statement = statement.where(Mt5Profile.owner_id == owner_id)
        statement = statement.order_by(Mt5Profile.is_active.desc(), Mt5Profile.updated_at.desc(), Mt5Profile.id.desc())
        return [self._serialize_profile(item) for item in db.scalars(statement).all()]

    def get_active_profile(self, db: Session, owner_id: str | None = None) -> Mt5Profile | None:
        statement = select(Mt5Profile).where(Mt5Profile.is_active.is_(True))
        if owner_id:
            statement = statement.where(Mt5Profile.owner_id == owner_id)
        statement = statement.order_by(Mt5Profile.updated_at.desc(), Mt5Profile.id.desc()).limit(1)
        return db.scalar(statement)

    def get_profile(self, db: Session, profile_id: int, owner_id: str | None = None) -> Mt5Profile:
        profile = db.get(Mt5Profile, profile_id)
        if profile is None:
            raise ValueError("MT5 profile not found.")
        if owner_id and profile.owner_id != owner_id:
            raise ValueError("MT5 profile does not belong to the requested owner.")
        return profile

    def create_profile(self, db: Session, payload: Mt5ProfileCreateRequest) -> dict[str, Any]:
        fernet = self._get_fernet()
        primary_symbol, normalized_symbols = self._resolve_primary_symbol(payload.primary_symbol, payload.symbols)

        if payload.set_active:
            self._deactivate_owner_profiles(db, payload.owner_id or "local")

        profile = Mt5Profile(
            owner_id=(payload.owner_id or "local").strip(),
            label=payload.label.strip(),
            login=int(payload.login),
            password_encrypted=fernet.encrypt(payload.password.encode("utf-8")).decode("utf-8"),
            server=payload.server.strip(),
            terminal_path=(payload.terminal_path or "").strip() or None,
            symbols_csv=",".join(normalized_symbols),
            volume_lots=float(payload.volume_lots),
            is_active=bool(payload.set_active),
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return self._serialize_profile(profile)

    def activate_profile(self, db: Session, profile_id: int, owner_id: str | None = None) -> dict[str, Any]:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)

        self._deactivate_owner_profiles(db, profile.owner_id)
        profile.is_active = True
        profile.updated_at = datetime.now(UTC)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return self._serialize_profile(profile)

    def deactivate_profile(
        self,
        db: Session,
        profile_id: int,
        owner_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        profile.is_active = False
        if error is not None:
            profile.last_connection_ok = False
            profile.last_connection_error = error[:255]
            profile.last_validated_at = datetime.now(UTC)
        profile.updated_at = datetime.now(UTC)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return self._serialize_profile(profile)

    def delete_profile(self, db: Session, profile_id: int, owner_id: str | None = None) -> None:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        if profile.is_active:
            raise ValueError("Cannot delete the active MT5 profile. Activate another profile first.")
        db.delete(profile)
        db.commit()

    def apply_active_profile(
        self,
        db: Session,
        runtime_settings: Settings | None = None,
        trading_service: Any | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        profile = self.get_active_profile(db, owner_id=owner_id)
        if profile is None:
            return None

        target_settings = runtime_settings or self.settings
        self._apply_runtime_update(target_settings, self._build_runtime_update(profile), trading_service=trading_service)

        return self._serialize_profile(profile)

    async def activate_runtime_profile(
        self,
        db: Session,
        profile_id: int,
        runtime_settings: Settings | None = None,
        trading_service: Any | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        validation = await self._validate_profile_connection(profile)
        self._persist_validation_result(db, profile, validation)
        if not validation["ok"]:
            raise ValueError(f"MT5 profile connection test failed: {validation['error']}")

        target_settings = runtime_settings or self.settings
        runtime_snapshot = self.capture_runtime_state(target_settings)
        runtime_update = self._build_runtime_update(profile)

        try:
            self._apply_runtime_update(target_settings, runtime_update, trading_service=trading_service)
        except Exception:
            self.restore_runtime_state(target_settings, runtime_snapshot, trading_service=trading_service)
            raise

        return self.activate_profile(db, profile_id, owner_id=owner_id)

    async def apply_saved_runtime_profile_if_valid(
        self,
        db: Session,
        runtime_settings: Settings | None = None,
        trading_service: Any | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        profile = self.get_active_profile(db, owner_id=owner_id)
        if profile is None:
            return None

        validation = await self._validate_profile_connection(profile)
        self._persist_validation_result(db, profile, validation)
        if not validation["ok"]:
            self.deactivate_profile(db, profile.id, owner_id=owner_id, error=validation["error"])
            return None

        return self.apply_active_profile(
            db,
            runtime_settings=runtime_settings,
            trading_service=trading_service,
            owner_id=owner_id,
        )

    async def apply_profile_if_valid(
        self,
        db: Session,
        profile_id: int,
        runtime_settings: Settings | None = None,
        trading_service: Any | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        validation = await self._validate_profile_connection(profile)
        self._persist_validation_result(db, profile, validation)
        if not validation["ok"]:
            return None

        target_settings = runtime_settings or self.settings
        self._apply_runtime_update(target_settings, self._build_runtime_update(profile), trading_service=trading_service)
        return self._serialize_profile(profile)

    async def test_connection(self, payload: Mt5ProfileCreateRequest) -> dict[str, Any]:
        temp_settings = self.settings.model_copy(
            update={
                **self._build_payload_runtime_update(payload),
            }
        )
        client = self.mt5_client_factory(temp_settings)
        try:
            account = await client.get_account_info()
            active_positions = None
            if hasattr(client, "get_active_positions_count"):
                active_positions = await client.get_active_positions_count()
            return {
                "ok": True,
                "account": {
                    "equity": float(account.get("equity") or 0.0),
                    "balance": float(account.get("balance") or 0.0),
                },
                "active_positions": active_positions,
                "server": payload.server.strip(),
                "login": int(payload.login),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "server": payload.server.strip(),
                "login": int(payload.login),
            }

    async def test_saved_profile(self, db: Session, profile_id: int, owner_id: str | None = None) -> dict[str, Any]:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        validation = await self._validate_profile_connection(profile)
        self._persist_validation_result(db, profile, validation)
        response: dict[str, Any] = {
            "ok": validation["ok"],
            "profile": self._serialize_profile(profile),
        }
        if validation["ok"]:
            response["account"] = validation["account"]
            response["active_positions"] = validation["active_positions"]
        else:
            response["error"] = validation["error"]
        return response

    def build_preview_settings_from_payload(self, payload: Mt5ProfileCreateRequest) -> Settings:
        return self.settings.model_copy(update=self._build_payload_runtime_update(payload))

    def build_preview_settings_from_profile(self, db: Session, profile_id: int, owner_id: str | None = None) -> tuple[Settings, dict[str, Any]]:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        serialized = self._serialize_profile(profile)
        return self.settings.model_copy(update=self._build_runtime_update(profile)), serialized

    def capture_runtime_state(self, runtime_settings: Settings | None = None) -> dict[str, Any]:
        target_settings = runtime_settings or self.settings
        return {
            "mt5_terminal_path": target_settings.mt5_terminal_path,
            "mt5_login": target_settings.mt5_login,
            "mt5_password": target_settings.mt5_password,
            "mt5_server": target_settings.mt5_server,
            "mt5_symbol": target_settings.mt5_symbol,
            "mt5_symbols": target_settings.mt5_symbols,
            "market_data_symbol": target_settings.market_data_symbol,
            "mt5_volume_lots": target_settings.mt5_volume_lots,
        }

    def restore_runtime_state(
        self,
        runtime_settings: Settings | None,
        runtime_state: dict[str, Any],
        trading_service: Any | None = None,
    ) -> None:
        target_settings = runtime_settings or self.settings
        self._apply_runtime_update(target_settings, runtime_state, trading_service=trading_service)

    def _deactivate_owner_profiles(self, db: Session, owner_id: str) -> None:
        statement = select(Mt5Profile).where(Mt5Profile.owner_id == owner_id, Mt5Profile.is_active.is_(True))
        for profile in db.scalars(statement).all():
            profile.is_active = False
            profile.updated_at = datetime.now(UTC)
            db.add(profile)
        db.flush()

    def _get_fernet(self) -> Fernet:
        raw_key = self.settings.mt5_profile_encryption_key.strip()
        if not raw_key:
            raise ValueError("MT5 profile encryption key is not configured. Set MT5_PROFILE_ENCRYPTION_KEY to a Fernet key.")
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception as exc:
            raise ValueError("MT5 profile encryption key is invalid. Use Fernet.generate_key() output.") from exc

    def _normalize_symbols(self, symbols: list[str]) -> list[str]:
        return [item.strip() for item in symbols if item.strip()]

    def _resolve_primary_symbol(self, primary_symbol: str | None, symbols: list[str]) -> tuple[str | None, list[str]]:
        resolved_symbols: list[str] = []
        candidate = (primary_symbol or "").strip() or None
        if candidate:
            resolved_symbols.append(candidate)
        for item in self._normalize_symbols(symbols):
            if item not in resolved_symbols:
                resolved_symbols.append(item)
        resolved_primary = resolved_symbols[0] if resolved_symbols else None
        return resolved_primary, resolved_symbols

    def _decrypt_password(self, encrypted_value: str) -> str:
        fernet = self._get_fernet()
        return fernet.decrypt(encrypted_value.encode("utf-8")).decode("utf-8")

    async def _validate_profile_connection(self, profile: Mt5Profile) -> dict[str, Any]:
        temp_settings = self.settings.model_copy(update=self._build_runtime_update(profile))
        client = self.mt5_client_factory(temp_settings)
        try:
            account = await client.get_account_info()
            active_positions = None
            if hasattr(client, "get_active_positions_count"):
                active_positions = await client.get_active_positions_count()
            return {
                "ok": True,
                "account": {
                    "equity": float(account.get("equity") or 0.0),
                    "balance": float(account.get("balance") or 0.0),
                },
                "active_positions": active_positions,
                "error": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "account": None,
                "active_positions": None,
                "error": str(exc),
            }

    def _persist_validation_result(self, db: Session, profile: Mt5Profile, validation: dict[str, Any]) -> None:
        validated_at = datetime.now(UTC)
        profile.last_connection_ok = bool(validation["ok"])
        profile.last_connection_error = None if validation["ok"] else str(validation["error"] or "Connection test failed")[:255]
        profile.last_validated_at = validated_at
        profile.updated_at = validated_at
        db.add(profile)
        db.commit()
        db.refresh(profile)

    def _apply_runtime_update(
        self,
        target_settings: Settings,
        runtime_update: dict[str, Any],
        trading_service: Any | None = None,
    ) -> None:
        for key, value in runtime_update.items():
            setattr(target_settings, key, value)

        if trading_service is None:
            return

        trading_service.settings = target_settings
        trading_service.mt5 = self.mt5_client_factory(target_settings)
        if hasattr(trading_service, "mt5_execution"):
            trading_service.mt5_execution.settings = target_settings
            trading_service.mt5_execution.mt5 = trading_service.mt5

    def _build_payload_runtime_update(self, payload: Mt5ProfileCreateRequest) -> dict[str, Any]:
        primary_symbol, normalized_symbols = self._resolve_primary_symbol(payload.primary_symbol, payload.symbols)
        market_data_symbol = self.settings.market_data_symbol
        if self.settings.effective_market_data_provider == "mt5":
            market_data_symbol = primary_symbol or self.settings.mt5_symbol
        return {
            "mt5_terminal_path": (payload.terminal_path or self.settings.mt5_terminal_path).strip(),
            "mt5_login": int(payload.login),
            "mt5_password": payload.password,
            "mt5_server": payload.server.strip(),
            "mt5_symbol": primary_symbol or self.settings.mt5_symbol,
            "mt5_symbols": ",".join(normalized_symbols),
            "market_data_symbol": market_data_symbol,
            "mt5_volume_lots": float(payload.volume_lots),
        }

    def _build_runtime_update(self, profile: Mt5Profile) -> dict[str, Any]:
        primary_symbol, normalized_symbols = self._resolve_primary_symbol(None, profile.symbols_csv.split(",") if profile.symbols_csv else [])
        market_data_symbol = self.settings.market_data_symbol
        if self.settings.effective_market_data_provider == "mt5":
            market_data_symbol = primary_symbol or self.settings.mt5_symbol
        return {
            "mt5_terminal_path": profile.terminal_path or self.settings.mt5_terminal_path,
            "mt5_login": int(profile.login),
            "mt5_password": self._decrypt_password(profile.password_encrypted),
            "mt5_server": profile.server,
            "mt5_symbol": primary_symbol or self.settings.mt5_symbol,
            "mt5_symbols": ",".join(normalized_symbols),
            "market_data_symbol": market_data_symbol,
            "mt5_volume_lots": float(profile.volume_lots),
        }

    def _serialize_profile(self, profile: Mt5Profile) -> dict[str, Any]:
        primary_symbol, normalized_symbols = self._resolve_primary_symbol(None, profile.symbols_csv.split(",") if profile.symbols_csv else [])
        return {
            "id": profile.id,
            "owner_id": profile.owner_id,
            "label": profile.label,
            "login": profile.login,
            "server": profile.server,
            "terminal_path": profile.terminal_path,
            "primary_symbol": primary_symbol,
            "symbols": normalized_symbols,
            "volume_lots": float(profile.volume_lots),
            "is_active": bool(profile.is_active),
            "password_configured": bool(profile.password_encrypted),
            "password_mask": "********" if profile.password_encrypted else "",
            "last_connection_ok": profile.last_connection_ok,
            "last_connection_error": profile.last_connection_error,
            "last_validated_at": profile.last_validated_at.isoformat() if profile.last_validated_at else None,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }