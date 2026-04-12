# Beacon Strategy Notes

## 2026-04-06 deployment hardening

- Operator-only routes are now separated from public mobile auth and mobile profile routes.
- CORS, trusted hosts, gzip, and optional HTTPS redirect are configurable from environment variables.
- Alembic migrations are now the supported schema path.
- Mobile auth rate limiting is now persisted in the database rather than held in process memory.
- Only `MT5_RUNTIME_OWNER_ID` can rewrite the shared process MT5 runtime; other mobile users remain owner-scoped.
- MT5 execution now supports `direct` and `queue` modes so personal single-host testing can evolve into worker-backed multi-user development without rewriting the trading service.

## Configured strategy stack

Beacon currently runs these configured strategies:

- trend-following
- breakout
- scalping
- mean-reversion
- momentum
- smart-money-concepts
- grid
- sentiment-bias
- pattern-heuristic
- multi-timeframe-confluence
- pivot-breakout

## Current operating model

Beacon is execution-oriented by design:

- it collapses the strategy stack into a weighted vote
- it primarily operates on one configured candle interval for live decisions
- it does not produce a formal analyst-style report with key levels, TP ladders, invalidation rules, and deployment guidance

## Beacon enhancement layer

Beacon keeps its own MT5 symbols, credentials, execution safeguards, and strategy stack, and adds a higher-order analysis layer that:

- reads multiple MT5 timeframes
- aggregates higher and lower timeframe context
- produces structured trade ideas and risk notes
- outputs bot-ready implementation guidance inside the Beacon project itself