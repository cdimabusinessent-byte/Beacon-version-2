# Windows MT5 Worker Deployment

This guide covers the Windows execution host for Beacon when the API tier runs separately on Google Cloud or another Linux hosting platform.

## Purpose

The Windows worker is responsible for:

- Running the MetaTrader terminal locally
- Reading queued MT5 jobs from the shared database
- Executing those jobs against the broker account
- Writing fill and failure results back to the same database

The API tier should stay in `MT5_EXECUTION_MODE=queue`. The worker is the only place that should use direct MT5 execution in a split deployment.

## Host Requirements

- Windows 10/11 or Windows Server
- Installed MetaTrader 5 terminal
- Python 3.12 compatible with this repo
- Network access to the shared PostgreSQL database
- Stable outbound access to the broker server
- Dedicated Windows user/session for the MT5 terminal

## Recommended Topology

- Cloud Run API tier writes `mt5_execution_jobs`
- Cloud SQL PostgreSQL stores app state and queued execution jobs
- Windows worker polls the same database and processes queued jobs

## Worker Environment

Create a worker-specific env file on the Windows host. Do not reuse the Cloud Run API env file.

Starter file:

- Copy `.env.worker.example` to a private worker-only env file such as `.env.worker`.

Recommended baseline:

```env
APP_NAME=Beacon
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://beacon_user:change_me@YOUR_CLOUD_SQL_HOST:5432/beacon
DATABASE_RUN_MIGRATIONS_ON_STARTUP=false
STARTUP_SELF_CHECK_REQUIRED=false
CONTROL_API_KEY=
CONTROL_ALLOWED_IPS=127.0.0.1,::1
TRUSTED_HOSTS=127.0.0.1,localhost
CORS_ALLOWED_ORIGINS=
CORS_ALLOWED_ORIGIN_REGEX=
CORS_ALLOW_CREDENTIALS=true
HTTPS_REDIRECT_ENABLED=false
GZIP_MINIMUM_SIZE=500

MARKET_DATA_PROVIDER=MT5
EXECUTION_PROVIDER=MT5
DRY_RUN=false
LIVE_TRADING_ARMED=false
AUTO_TRADING_ENABLED=false
RECONCILIATION_ENABLED=false

MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_PROFILE_ENCRYPTION_KEY=replace-with-fernet-key
MT5_RUNTIME_OWNER_ID=local
MT5_EXECUTION_MODE=queue
MT5_WORKER_POLL_SECONDS=5
MT5_WORKER_CLAIM_TIMEOUT_SECONDS=120
MT5_WORKER_KEY=worker-local-1
MT5_WORKER_LABEL=Local MT5 Worker
MT5_LOGIN=your-login
MT5_PASSWORD=your-password
MT5_SERVER=your-broker-server
MT5_SYMBOL=GBPUSDm
MT5_SYMBOLS=EURUSDm,GBPUSDm,USDJPYm,USDCHFm,AUDUSDm,USDCADm,NZDUSDm,EURJPYm,GBPJPYm,GBPCHFm,EURAUDm,EURGBPm,AUDJPYm,AUDCHFm,CADJPYm,CHFJPYm,NZDJPYm,XAUUSDm
MT5_VOLUME_LOTS=0.01
MT5_DEVIATION=15
MT5_MAGIC=20260326
```

## Setup Steps

1. Clone the repo onto the Windows worker host.
2. Create and activate a virtual environment.
3. Install dependencies with `pip install -r requirements.txt`.
4. Place the worker env file on disk.
5. Set the environment variable `BEACON_ENV_FILE` to that worker env file path.
6. Run `python -m alembic upgrade head` once against the shared database.
7. Confirm the MT5 terminal can initialize and log in under the same Windows user account.
8. Provision the worker key and owner/profile binding through the API before starting the process.

Example provisioning call from an operator workstation:

```powershell
./provision_Beacon_MT5_Workers.ps1 \
	-ApiBaseUrl https://YOUR_RUN_APP_HOST \
	-OwnerId trader-alpha \
	-WorkerKey worker-trader-alpha-1 \
	-ProfileId 1 \
	-Label "Trader Alpha Windows Worker" \
	-TerminalPath "C:\Program Files\MetaTrader 5\terminal64.exe" \
	-ControlApiKey YOUR_CONTROL_API_KEY
```

## Start Command

Run the worker with the selected env file:

```powershell
./start_MT5_Worker.ps1 \
	-EnvFile C:\Path\To\.env.worker \
	-OwnerId trader-alpha \
	-WorkerKey worker-trader-alpha-1 \
	-WorkerLabel "Trader Alpha Windows Worker"
```

## Operational Notes

- Do not point browser traffic or public API traffic at the worker host.
- Keep only one worker per isolated execution context unless you intentionally coordinate multiple workers.
- Keep MT5 credentials only on the Windows worker, not in the Cloud Run API env.
- If you need multiple broker accounts, use separate workers and separate runtime ownership boundaries.
- Set `MT5_RUNTIME_OWNER_ID` and `MT5_WORKER_KEY` explicitly for each worker process so queued jobs stay pinned to the intended owner/profile.
- Use Windows Task Scheduler, NSSM, or a service wrapper to keep the worker running after reboots.

## Validation Checklist

1. API tier is using `MT5_EXECUTION_MODE=queue`.
2. Cloud SQL connectivity works from both the API tier and the Windows worker.
3. Worker can initialize MetaTrader and read account info.
4. A queued execution job moves from `QUEUED` to `FILLED` or `FAILED`.
5. Dashboard and reconciliation reflect worker-processed results.
6. `GET /api/mt5/workers?owner_id=OWNER_ID` shows the worker with the expected `worker_key`, `profile_id`, and recent heartbeat.
