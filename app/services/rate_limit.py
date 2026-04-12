from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthRateLimitEntry


class AuthRateLimiter:
    def __init__(self, max_attempts: int = 10, window_seconds: int = 60):
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(1, int(window_seconds))

    def retry_after_seconds(self, db: Session, key: str) -> int | None:
        now = datetime.now(UTC)
        self._purge_stale_rows(db, now)
        entry = db.scalar(select(AuthRateLimitEntry).where(AuthRateLimitEntry.key == key))
        if entry is None:
            return None

        blocked_until = self._as_utc(entry.blocked_until)
        window_started_at = self._as_utc(entry.window_started_at)

        if blocked_until and blocked_until > now:
            return max(1, int((blocked_until - now).total_seconds()))

        if now - window_started_at >= timedelta(seconds=self.window_seconds):
            db.delete(entry)
            db.commit()
            return None
        return None

    def record_failure(self, db: Session, key: str) -> None:
        now = datetime.now(UTC)
        self._purge_stale_rows(db, now)
        entry = db.scalar(select(AuthRateLimitEntry).where(AuthRateLimitEntry.key == key))
        if entry is None:
            entry = AuthRateLimitEntry(key=key, attempts=1, window_started_at=now, blocked_until=None)
            db.add(entry)
            db.commit()
            return

        window_started_at = self._as_utc(entry.window_started_at)
        if now - window_started_at >= timedelta(seconds=self.window_seconds):
            entry.attempts = 1
            entry.window_started_at = now
            entry.blocked_until = None
        else:
            entry.attempts += 1
            if entry.attempts >= self.max_attempts:
                entry.blocked_until = window_started_at + timedelta(seconds=self.window_seconds)
        entry.updated_at = now
        db.add(entry)
        db.commit()

    def reset(self, db: Session, key: str) -> None:
        entry = db.scalar(select(AuthRateLimitEntry).where(AuthRateLimitEntry.key == key))
        if entry is None:
            return
        db.delete(entry)
        db.commit()

    def _purge_stale_rows(self, db: Session, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(seconds=self.window_seconds)
        stale_rows = []
        for row in db.scalars(select(AuthRateLimitEntry)).all():
            window_started_at = self._as_utc(row.window_started_at)
            blocked_until = self._as_utc(row.blocked_until)
            if blocked_until is None and window_started_at <= cutoff:
                stale_rows.append(row)
                continue
            if blocked_until is not None and blocked_until <= current:
                stale_rows.append(row)
        if not stale_rows:
            return 0
        for row in stale_rows:
            db.delete(row)
        db.commit()
        return len(stale_rows)

    def _as_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)