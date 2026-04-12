from __future__ import annotations

from threading import Lock
from time import time


class ObservabilityStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.counters: dict[str, float] = {
            "bot_cycles_total": 0.0,
            "bot_cycle_failures_total": 0.0,
            "bot_missed_cycles_total": 0.0,
            "bot_reconciliation_runs_total": 0.0,
            "bot_reconciliation_failures_total": 0.0,
            "bot_order_rejects_total": 0.0,
            "bot_spread_blowouts_total": 0.0,
            "bot_drawdown_blocks_total": 0.0,
            "bot_stale_market_data_total": 0.0,
            "bot_hedge_cooldown_blocks_total": 0.0,
            "bot_hedge_max_attempt_blocks_total": 0.0,
            "bot_hedge_min_delta_blocks_total": 0.0,
        }
        self.gauges: dict[str, float] = {
            "bot_last_cycle_timestamp": 0.0,
            "bot_last_reconciliation_timestamp": 0.0,
        }

    def increment(self, key: str, amount: float = 1.0) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0.0) + amount

    def set_gauge(self, key: str, value: float) -> None:
        with self._lock:
            self.gauges[key] = value

    def touch_cycle(self) -> None:
        self.set_gauge("bot_last_cycle_timestamp", time())

    def touch_reconciliation(self) -> None:
        self.set_gauge("bot_last_reconciliation_timestamp", time())

    def render_prometheus(self) -> str:
        # Minimal Prometheus exposition format output.
        lines: list[str] = []
        with self._lock:
            for key, value in sorted(self.counters.items()):
                lines.append(f"# TYPE {key} counter")
                lines.append(f"{key} {value}")
            for key, value in sorted(self.gauges.items()):
                lines.append(f"# TYPE {key} gauge")
                lines.append(f"{key} {value}")
        lines.append("")
        return "\n".join(lines)
