# Beacon

FastAPI trading bot with a simple RSI strategy, pluggable live market data, SQLite trade persistence, and a small HTML dashboard.

## Beacon Overview

Beacon is a standalone FastAPI trading and analysis application for desktop deployment with institutional-style professional FX analysis on top of an MT5 execution stack. It keeps its own MT5 symbol universe, execution settings, and broker details inside its own `.env`, then generates:

- Multi-timeframe analysis for `1m`, `5m`, `15m`, `1h`, `4h`, and `1d`
- Structured trade plans with entry, SL, TP1, TP2, TP3, and risk-to-reward
- Account-size-aware risk notes using MT5 symbol specifications
- Bot logic and deployment guidance for MT5/VPS/web execution

Endpoint:

- `POST /api/analysis/pro`

Example payload:

```json
{
	"symbols": ["GBPUSD+"],
	"account_size": 20000,
	"risk_tolerance": "MEDIUM",
	"trading_style": "DAY TRADING"
}
```

The Beacon dashboard includes a `Professional FX Analysis` panel that calls the same endpoint.

## Architecture

- `app/main.py`: FastAPI app, dashboard routes, health endpoint, and optional background loop.
- `app/services/binance.py`: REST client for Binance market data, exchange rules, account lookups, and live market orders.
- `app/services/coinbase.py`: Public Coinbase Exchange market-data client for live candle testing without Binance auth.
- `app/services/trading.py`: RSI signal evaluation, position tracking, order execution, and trade history queries.
- `app/models.py`: SQLAlchemy trade model persisted to SQLite.
- `app/templates/dashboard.html`: HTML dashboard for status, manual runs, and recent trades.

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`.
4. If you plan to store MT5 user profiles in the dashboard, set `MT5_PROFILE_ENCRYPTION_KEY` to a Fernet key so passwords are encrypted at rest.
5. For public live data testing, leave `MARKET_DATA_PROVIDER=auto` so the bot tries OKX, Kraken, Coinbase, then Binance automatically.
6. Apply database migrations with `alembic upgrade head`.
7. Run `./start_Beacon.ps1` or `uvicorn app.main:app --reload`.
8. Open `http://127.0.0.1:8000`.

## Windows Launchers

- `start_Beacon.ps1` is the general launcher and supports `-Reload` for active development.
- `start_Beacon_Production.ps1` is the dedicated production-style launcher for a stable local Beacon runtime on Windows. It runs migrations, starts uvicorn without reload, and can open the dashboard automatically with `-OpenBrowser`.
- `create_Beacon_Desktop_Shortcut.ps1` creates a desktop shortcut named `Beacon Production.lnk` that launches the production script in a PowerShell window.

Both Windows launchers now support `-EnvFile` so you can switch between separate local and hosting configs without renaming `.env`.

Example local laptop test run:

```powershell
./start_Beacon_Production.ps1 -EnvFile .env.localtest -OpenBrowser
```

Example default hosted-style local run:

```powershell
./start_Beacon_Production.ps1 -EnvFile .env -OpenBrowser
```

Example desktop shortcut setup:

```powershell
./create_Beacon_Desktop_Shortcut.ps1
```

Example production launch:

```powershell
./start_Beacon_Production.ps1 -OpenBrowser
```

## Safety Defaults

- `DRY_RUN=true` keeps all trades simulated while still writing them to the database.
- Live order placement only happens when both Binance credentials are present and `DRY_RUN=false`.
- The dashboard can manually trigger strategy execution with `POST /api/bot/run`.
- The dashboard now includes an encrypted `MT5 Profile Intake` panel for storing masked MT5 login/server/profile metadata ahead of mobile or multi-user clients.
- The MT5 profile form now has a dedicated `Primary Broker Symbol` field so each user can enter the exact raw MT5 symbol used by their broker, including suffixes like `m`, `z`, or `+`.
- `BINANCE_HTTP_TRUST_ENV` lets you decide whether the bot should inherit proxy environment variables for outbound Binance requests.
- `MARKET_DATA_PROVIDER=auto` tries OKX, Kraken, Coinbase, then Binance until one responds.
- Leave `MARKET_DATA_SYMBOL` blank when using `auto`, so the bot can convert symbols correctly for each provider.
- You can still force one provider manually with `okx`, `kraken`, `coinbase`, or `binance`.
- `RISK_MIN_SECONDS_BETWEEN_TRADES` (default `0`) adds a per-symbol cooldown to prevent immediate re-entries.
- `RISK_MAX_QUOTE_EXPOSURE_PCT` (default `0`) caps each live Binance buy as a percentage of free quote balance.
- `RISK_MAX_LOSS_PER_TRADE_PCT` (default `0`) caps live Binance buy size so projected stop-loss loss stays within budget.
- `RISK_CORRELATION_CAP` (default `0`) blocks new exposure when symbol return correlation exceeds the cap.
- `RISK_PORTFOLIO_VAR_LIMIT_PCT` and `RISK_PORTFOLIO_ES_LIMIT_PCT` (default `0`) block new exposure when historical portfolio VaR/ES exceeds the configured limit.
- `RISK_VAR_CONFIDENCE` and `RISK_VAR_LOOKBACK_CANDLES` tune VaR/ES confidence level and historical depth.
- `RISK_VOLATILITY_TARGET_PCT` (default `0`) scales Binance live buy size inversely with realized return volatility so each trade targets a consistent daily P&L volatility budget as a percentage of free balance.
- `RISK_VOL_LOOKBACK_CANDLES` (default `30`) sets the candle window used to compute realized volatility for the volatility-targeted sizing cap.
- `RISK_MAX_POSITION_SIZE_QUOTE` (default `0`) enforces a hard per-position notional cap before each buy.
- `RISK_MAX_PORTFOLIO_EXPOSURE_QUOTE` (default `0`) enforces a hard total portfolio exposure cap before each buy.
- `RISK_MAX_CONCURRENT_POSITIONS` (default `0`) limits how many symbols can be open simultaneously.
- `RISK_DAILY_LOSS_KILL_SWITCH_PCT` (default `0`) halts new entries once daily drawdown crosses the kill-switch threshold.
- `RISK_MAX_SPREAD_PCT` and `RISK_MAX_SLIPPAGE_PCT` (default `0`) enforce spread/slippage guards for both Binance and MT5.
- Strategy names are intentionally conservative: `sentiment-bias` and `pattern-heuristic` replace stronger claims unless external news feeds or validated ML pipelines are integrated.

## Reconciliation And Observability

- `RECONCILIATION_ENABLED=true` starts a background broker reconciliation daemon (live mode only).
- `RECONCILIATION_INTERVAL_SECONDS` controls how often balances, positions, open orders, fills, rejected requests, and orphaned stops are refreshed.
- `STALE_MARKET_DATA_SECONDS` configures stale market data threshold handling.
- On startup (live mode), the bot hydrates authoritative position state from broker reconciliation before running trading loops.
- Reconciliation persists separate fill and position journals (`broker_fill_journal`, `broker_position_journal`) so the database is an audit trail while broker state remains authoritative.
- `GET /api/reconciliation/status` returns latest reconciliation result and errors.
- `GET /metrics` exposes Prometheus-style bot metrics (cycles, failures, missed cycles, reconciliation runs, spread/drawdown/stale-data counters).

## Deployment Notes

- Admin/operator routes now stay behind `CONTROL_ALLOWED_IPS` and optional `CONTROL_API_KEY`, including the dashboard, execution journal, reconciliation status, and Prometheus metrics.
- Public mobile routes are session-based and no longer require the operator control key.
- Mobile auth rate limiting is now database-backed, so throttling survives process restarts and works correctly with multiple app instances that share the same database.
- Configure `CORS_ALLOWED_ORIGINS` or `CORS_ALLOWED_ORIGIN_REGEX` before serving a separate web frontend.
- Configure `TRUSTED_HOSTS` for your deployed domains and set `HTTPS_REDIRECT_ENABLED=true` when Beacon is directly responsible for HTTP to HTTPS redirection.
- Beacon now runs Alembic migrations on startup by default. Use `DATABASE_RUN_MIGRATIONS_ON_STARTUP=false` only if your deployment handles migrations separately.
- For multi-user mobile/web deployments, keep process-wide MT5 execution bound to `MT5_RUNTIME_OWNER_ID` only. User-owned mobile profiles are stored and activated per owner, but they no longer rewrite the shared server runtime.
- `MT5_EXECUTION_MODE=direct` keeps the current single-host Windows execution path for personal testing.
- `MT5_EXECUTION_MODE=queue` stores MT5 execution jobs in the database so a separate Windows worker can process them later.
- Run the worker with `python -m app.mt5_worker` on the Windows MT5 host when queue mode is enabled.

## Production Topology

- Web frontend: host a separate browser client against Beacon's public API with `CORS_ALLOWED_ORIGINS` set to the frontend origin.
- Mobile app: use the `/api/mobile/auth/*` and `/api/mobile/mt5/*` routes against the same API tier. Do not ship `CONTROL_API_KEY` in mobile or browser clients.
- API tier: run FastAPI behind TLS termination and a reverse proxy, with PostgreSQL as the shared database for users, sessions, rate-limit rows, and trading journals.
- MT5 execution tier: keep real MT5 execution on a Windows host or VPS with the MetaTrader terminal installed. For multi-tenant live execution, the next step is a dedicated worker layer so each MT5 account runs in its own isolated process instead of sharing one API runtime.

## Google Cloud Topology

- Cloud Run API tier: build from `Dockerfile` and deploy the FastAPI app as a Linux container.
- Cloud SQL: use PostgreSQL and set `DATABASE_URL` with the `psycopg` driver.
- Secret Manager: store `CONTROL_API_KEY`, `MT5_PROFILE_ENCRYPTION_KEY`, Telegram tokens, and any future broker credentials outside `.env`.
- Windows MT5 worker: keep real MT5 terminal execution on a Windows VM or VPS. Do not try to execute MT5 directly inside Cloud Run.
- Queue mode: set `MT5_EXECUTION_MODE=queue` in the API tier so Cloud Run writes execution jobs while the Windows worker consumes them with `python -m app.mt5_worker`.

Recommended files for this topology:

- `.env.gcp.api.example`: safe baseline env template for the Linux API tier.
- `.env.worker.example`: worker-only env template for the Windows MT5 execution host.
- `provision_Beacon_MT5_Workers.ps1`: provisions or updates owner-bound worker keys through the API.
- `managed_workers.staging.example.json`: example batch config for staging worker provisioning.
- `start_MT5_Worker.ps1`: starts one Windows worker with a selected env file and explicit owner/worker identity overrides.
- `smoke_test_managed_workers.py`: deterministic local smoke test for two-owner queue isolation.
- `cloudrun.service.yaml`: starter service manifest for `gcloud run services replace`.
- `cloudbuild.yaml`: Cloud Build pipeline that builds, pushes, and deploys the Cloud Run service.
- `Dockerfile`: container image for Cloud Run.
- `STAGING_GCP_ROLLOUT_RUNBOOK.md`: exact staging rollout order from secrets and deploy to worker validation.
- `WINDOWS_MT5_WORKER_DEPLOYMENT.md`: dedicated runbook for the Windows MT5 execution host.

Example deployment flow:

```bash
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/REPOSITORY/beacon:latest
gcloud run services replace cloudrun.service.yaml --region REGION
```

One-command Cloud Build deployment:

```bash
gcloud builds submit \
	--config cloudbuild.yaml \
	--substitutions _REGION=us-central1,_REPOSITORY=beacon,_SERVICE=beacon-api
```

Windows PowerShell helper for staging deploys:

```powershell
./deploy_Beacon_GCP_Staging.ps1 `
	-ProjectId your-gcp-project `
	-CloudSqlInstance beacon-staging-sql `
	-TrustedHosts "beacon-api-xyz.a.run.app,api.example.com" `
	-CorsAllowedOrigins "https://app.example.com" `
	-Region us-central1 `
	-Repository beacon `
	-ServiceName beacon-api `
	-CreateArtifactRegistryIfMissing
```

The helper script:

- renders a temporary Cloud Run manifest with your project-specific placeholders
- optionally creates the Artifact Registry repository if it is missing
- submits `cloudbuild.yaml` with the right substitutions
- removes the temporary rendered manifest after the build submission finishes

Before the first deployment:

```bash
gcloud artifacts repositories create beacon --repository-format=docker --location=us-central1
gcloud secrets create beacon-database-url --replication-policy=automatic
gcloud secrets create beacon-control-api-key --replication-policy=automatic
gcloud secrets create beacon-mt5-profile-encryption-key --replication-policy=automatic
```

Populate the secrets and update `cloudrun.service.yaml` placeholders for:

- `PROJECT_ID`
- `REGION`
- `REPOSITORY`
- `CLOUDSQL_INSTANCE`
- `serviceAccountName`
- trusted host and CORS origin values

Cloud Build will:

- build the container image from `Dockerfile`
- push both `:$COMMIT_SHA` and `:latest` tags to Artifact Registry
- render `cloudrun.service.yaml` with the resolved image URI
- deploy the service to Cloud Run

Notes:

- Keep `MARKET_DATA_PROVIDER=auto` on Cloud Run. MT5 market data and MT5 direct execution remain Windows-only operational paths.
- Keep `LIVE_TRADING_ARMED=false` and `AUTO_TRADING_ENABLED=false` until the API tier, Cloud SQL connection, and Windows worker are validated in staging.
- Use `WINDOWS_MT5_WORKER_DEPLOYMENT.md` for the separate Windows execution host. Do not copy the Cloud Run API env file onto the MT5 worker.
- Use `STAGING_GCP_ROLLOUT_RUNBOOK.md` when you want the exact end-to-end staging execution order in one place.

First staged rollout order:

1. Prepare Cloud SQL and Secret Manager.
2. Deploy the API tier with `cloudbuild.yaml`.
3. Provision owner-bound worker keys with `provision_Beacon_MT5_Workers.ps1`.
4. Prepare one Windows worker env file per isolated execution owner from `.env.worker.example`.
5. Start each Windows worker with `start_MT5_Worker.ps1`.
6. Keep queue mode enabled and validate end-to-end job execution before changing any live-trading flags.

Example worker provisioning:

```powershell
./provision_Beacon_MT5_Workers.ps1 \
	-ApiBaseUrl https://YOUR_RUN_APP_HOST \
	-ConfigFile .\managed_workers.staging.example.json \
	-ControlApiKey YOUR_CONTROL_API_KEY
```

Example worker launch:

```powershell
./start_MT5_Worker.ps1 \
	-EnvFile .env.worker \
	-OwnerId trader-alpha \
	-WorkerKey worker-trader-alpha-1 \
	-WorkerLabel "Trader Alpha Windows Worker"
```

Local isolation smoke test:

```powershell
.\.venv\Scripts\python.exe smoke_test_managed_workers.py
```

## Dual Deployment Modes

- Direct mode:
	Keep `MT5_EXECUTION_MODE=direct`. Beacon places MT5 orders immediately from the same runtime. This is the right path for personal testing on a single Windows machine.
- Queue mode:
	Set `MT5_EXECUTION_MODE=queue`. Beacon records MT5 execution jobs in `mt5_execution_jobs`, marks trades as `QUEUED`, and leaves actual execution to `python -m app.mt5_worker` on the Windows MT5 host.
- Migration path:
	Start in direct mode for personal testing. When you move to multi-user development, switch to queue mode and deploy the worker without rewriting the trading service.

## Recommended Environment Baseline

- Use PostgreSQL for live web/mobile deployments instead of SQLite.
- Rotate all exposed secrets before deployment, especially any Telegram bot tokens or chat targets that were ever committed or stored in a shared `.env`.
- Set `TRUSTED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CONTROL_API_KEY`, and `MT5_PROFILE_ENCRYPTION_KEY` explicitly in production.
- Keep `LIVE_TRADING_ARMED=false` until the API tier, database, and MT5 worker topology are validated in staging.

## Notes

- This project targets Binance Spot trading pairs like `BTCUSDT`.
- Dry-run tests can use Coinbase market data while keeping the execution side on Binance for future live trading.
- If you enable live orders, verify exchange permissions, balances, and risk controls before trading real funds.

## Backtesting

Run the RSI strategy on historical candles from your configured market-data provider:

- Endpoint: `POST /api/backtest/run`
- Example:

```bash
curl -X POST "http://127.0.0.1:8000/api/backtest/run?history_limit=1000&initial_balance=1000"
```

Optional query parameters:

- `history_limit` (default `1000`): number of candles to evaluate.
- `initial_balance` (default `1000`): starting quote balance for the simulation.
- `trade_amount` (default `TRADE_AMOUNT_USDT`): allocation per buy signal.
- `fee_rate` (default `FEE_RATE`): per-trade fee used in the simulation.
- `market_data_symbol` (optional): override symbol for a single backtest run (useful for MT5 symbols like `EURUSD`).

Response includes ROI, net PnL, win rate, completed trades, and max drawdown.
The simulator is event-driven and models spread, slippage, latency, partial fills, fees, and minimum-notional constraints.
Output includes train/validation/out-of-sample windows, walk-forward runs, Monte Carlo ROI bands, and simulation assumptions.
Backtest output also includes a `trust_assessment` gate so parameter sets are flagged as trusted only when out-of-sample, walk-forward, and Monte Carlo checks are acceptable.

Batch MT5 symbol backtest:

- Endpoint: `POST /api/backtest/run-mt5-batch`
- Uses `MT5_SYMBOLS` from `.env` or accepts `symbols=EURUSD,GBPUSD,USDJPY`
- Example:

```bash
curl -X POST "http://127.0.0.1:8000/api/backtest/run-mt5-batch?history_limit=1000&symbols=EURUSD,GBPUSD,USDJPY"
```

## Execution Journal

- Each `Trade` journal row stores additional execution context: intended price, fill price, fee amount, slippage %, stop/take at entry, strategy weights, confidence, equity before/after, broker position id, and reconciliation status.
- Broker reconciliation updates journal reconciliation status (`PENDING`, `OPEN`, `MATCHED`, `UNMATCHED`, `SKIPPED`) while broker state remains the trading source of truth.
