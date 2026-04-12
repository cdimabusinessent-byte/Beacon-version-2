from __future__ import annotations

from datetime import UTC, datetime
from time import time
from typing import Any

import httpx

from app.config import Settings


class NewsCalendarClient:
    """Fetches and caches high-impact calendar events from an external URL.

    Expected provider payload shape:
    [
      {
        "timestamp": "2026-04-11T13:30:00Z",
        "currency": "GBP",
        "impact": "high",
        "title": "CPI Release"
      }
    ]
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cached_events: list[dict[str, Any]] = []
        self._cached_at: float = 0.0

    def _normalize_event(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        ts_raw = str(raw.get("timestamp") or raw.get("time") or "").strip()
        currency = str(raw.get("currency") or "").strip().upper()
        impact = str(raw.get("impact") or "").strip().lower()
        title = str(raw.get("title") or raw.get("name") or "event").strip()

        if not ts_raw or not currency or not impact:
            return None

        normalized_ts = ts_raw.replace("Z", "+00:00")
        try:
            timestamp = datetime.fromisoformat(normalized_ts)
        except ValueError:
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        return {
            "timestamp": timestamp,
            "currency": currency,
            "impact": impact,
            "title": title,
        }

    async def fetch_events(self) -> list[dict[str, Any]]:
        url = (self.settings.news_calendar_provider_url or "").strip()
        if not url:
            return []

        cache_seconds = max(0, int(self.settings.news_calendar_cache_seconds))
        now = time()
        if cache_seconds > 0 and self._cached_events and (now - self._cached_at) < cache_seconds:
            return list(self._cached_events)

        timeout = max(0.5, float(self.settings.news_calendar_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, list):
            return []

        events: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_event(item)
            if normalized is not None:
                events.append(normalized)

        self._cached_events = events
        self._cached_at = now
        return list(events)
