from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppUser, MobileSession
from app.schemas import MobileAuthLoginRequest, MobileAuthRegisterRequest


class MobileAuthService:
    def __init__(self, session_days: int = 30, max_active_sessions_per_user: int = 5, revoked_retention_days: int = 7):
        self.session_days = max(1, int(session_days))
        self.max_active_sessions_per_user = max(1, int(max_active_sessions_per_user))
        self.revoked_retention_days = max(1, int(revoked_retention_days))

    def register_user(self, db: Session, payload: MobileAuthRegisterRequest) -> dict[str, Any]:
        self.purge_stale_sessions(db)
        normalized_email = payload.email.strip().lower()
        existing = db.scalar(select(AppUser).where(AppUser.email == normalized_email))
        if existing is not None:
            raise ValueError("User already exists for that email.")

        owner_id = self._build_owner_id(normalized_email, db)
        user = AppUser(
            owner_id=owner_id,
            email=normalized_email,
            display_name=(payload.display_name or normalized_email.split("@")[0]).strip(),
            password_hash=self._hash_password(payload.password),
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        session = self._create_session(db, owner_id=user.owner_id, device_name=payload.device_name)
        return {
            "user": self._serialize_user(user),
            "session": session,
        }

    def login_user(self, db: Session, payload: MobileAuthLoginRequest) -> dict[str, Any]:
        self.purge_stale_sessions(db)
        normalized_email = payload.email.strip().lower()
        user = db.scalar(select(AppUser).where(AppUser.email == normalized_email))
        if user is None or not user.is_active:
            raise ValueError("Invalid email or password.")
        if not self._verify_password(payload.password, user.password_hash):
            raise ValueError("Invalid email or password.")

        session = self._create_session(db, owner_id=user.owner_id, device_name=payload.device_name)
        return {
            "user": self._serialize_user(user),
            "session": session,
        }

    def get_session_context(self, db: Session, raw_token: str) -> dict[str, Any]:
        self.purge_stale_sessions(db)
        if not raw_token.strip():
            raise ValueError("Session token is required.")
        token_hash = self._hash_token(raw_token)
        session = db.scalar(select(MobileSession).where(MobileSession.token_hash == token_hash))
        if session is None:
            raise ValueError("Invalid session token.")
        if session.revoked_at is not None:
            raise ValueError("Session has been revoked.")
        if session.expires_at <= datetime.now(UTC):
            raise ValueError("Session has expired.")

        user = db.scalar(select(AppUser).where(AppUser.owner_id == session.owner_id))
        if user is None or not user.is_active:
            raise ValueError("Session user is unavailable.")

        return {
            "owner_id": user.owner_id,
            "user": self._serialize_user(user),
            "session": self._serialize_session_record(session),
        }

    def revoke_session(self, db: Session, raw_token: str) -> dict[str, Any]:
        self.purge_stale_sessions(db)
        token_hash = self._hash_token(raw_token)
        session = db.scalar(select(MobileSession).where(MobileSession.token_hash == token_hash))
        if session is None:
            raise ValueError("Invalid session token.")
        session.revoked_at = datetime.now(UTC)
        db.add(session)
        db.commit()
        db.refresh(session)
        return self._serialize_session_record(session)

    def purge_stale_sessions(self, db: Session) -> int:
        now = datetime.now(UTC)
        retention_cutoff = now - timedelta(days=self.revoked_retention_days)
        stale_sessions = db.scalars(
            select(MobileSession).where(
                (MobileSession.expires_at <= now)
                | ((MobileSession.revoked_at.is_not(None)) & (MobileSession.revoked_at <= retention_cutoff))
            )
        ).all()
        if not stale_sessions:
            return 0

        for session in stale_sessions:
            db.delete(session)
        db.commit()
        return len(stale_sessions)

    def _create_session(self, db: Session, owner_id: str, device_name: str | None = None) -> dict[str, Any]:
        self._trim_active_sessions(db, owner_id)
        raw_token = secrets.token_urlsafe(32)
        session = MobileSession(
            owner_id=owner_id,
            token_hash=self._hash_token(raw_token),
            device_name=(device_name or "").strip() or None,
            expires_at=datetime.now(UTC) + timedelta(days=self.session_days),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return {
            **self._serialize_session_record(session),
            "token": raw_token,
        }

    def _trim_active_sessions(self, db: Session, owner_id: str) -> None:
        now = datetime.now(UTC)
        active_sessions = db.scalars(
            select(MobileSession)
            .where(
                MobileSession.owner_id == owner_id,
                MobileSession.revoked_at.is_(None),
                MobileSession.expires_at > now,
            )
            .order_by(MobileSession.created_at.asc(), MobileSession.id.asc())
        ).all()
        overflow = max(0, len(active_sessions) - self.max_active_sessions_per_user + 1)
        if overflow == 0:
            return

        for session in active_sessions[:overflow]:
            db.delete(session)
        db.flush()

    def _build_owner_id(self, email: str, db: Session) -> str:
        base = email.split("@")[0]
        normalized = "".join(char.lower() if char.isalnum() else "-" for char in base).strip("-") or "user"
        candidate = normalized[:64]
        suffix = 1
        while db.scalar(select(AppUser).where(AppUser.owner_id == candidate)) is not None:
            suffix += 1
            candidate = f"{normalized[:56]}-{suffix}"
        return candidate

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
        return f"{salt}${digest.hex()}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            salt, expected_hex = stored_hash.split("$", 1)
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
        return hmac.compare_digest(digest.hex(), expected_hex)

    def _hash_token(self, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def _serialize_user(self, user: AppUser) -> dict[str, Any]:
        return {
            "owner_id": user.owner_id,
            "email": user.email,
            "display_name": user.display_name,
            "is_active": bool(user.is_active),
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

    def _serialize_session_record(self, session: MobileSession) -> dict[str, Any]:
        return {
            "owner_id": session.owner_id,
            "device_name": session.device_name,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            "revoked_at": session.revoked_at.isoformat() if session.revoked_at else None,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }