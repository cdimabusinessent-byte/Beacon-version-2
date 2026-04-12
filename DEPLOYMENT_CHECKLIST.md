# Beacon Deployment Checklist

## Before Go-Live

1. Replace `DATABASE_URL` with PostgreSQL and run `alembic upgrade head` against that database.
2. Set `CONTROL_API_KEY`, `TRUSTED_HOSTS`, and `CORS_ALLOWED_ORIGINS` for the real deployed domains.
3. Keep `LIVE_TRADING_ARMED=false` until staging validation is complete.
4. Rotate any previously exposed secrets before adding them back to environment configuration.
5. Set `MT5_PROFILE_ENCRYPTION_KEY` and keep MT5 account credentials only on the dedicated Windows execution host.

## Web And Mobile Topology

1. Run the FastAPI API tier behind TLS termination and a reverse proxy.
2. Point browser and mobile clients only at the public API routes.
3. Do not ship `CONTROL_API_KEY` to browser or mobile clients.
4. Share one PostgreSQL database across all API instances.

## Google Cloud Readiness

1. Use `Dockerfile` for the API image and deploy only the FastAPI tier to Cloud Run.
2. Use `.env.gcp.api.example` as the starting point for Cloud Run environment variables.
3. Keep `MARKET_DATA_PROVIDER=auto` and `MT5_EXECUTION_MODE=queue` on Cloud Run.
4. Keep `LIVE_TRADING_ARMED=false` and `AUTO_TRADING_ENABLED=false` until staging is validated.
5. Store `CONTROL_API_KEY`, `MT5_PROFILE_ENCRYPTION_KEY`, and all broker or notification secrets in Secret Manager.
6. Deploy PostgreSQL on Cloud SQL and point `DATABASE_URL` at it.
7. Keep the MT5 worker on a Windows VM or external Windows host; Cloud Run is not the MT5 execution tier.
8. Use `cloudbuild.yaml` for repeatable build and deploy automation.
9. Use `WINDOWS_MT5_WORKER_DEPLOYMENT.md` for the separate worker host; do not mirror the Cloud Run API env there.
10. Use `.env.worker.example` as the starting point for the Windows worker env file.
11. Use `deploy_Beacon_GCP_Staging.ps1` if you want a parameterized PowerShell deploy path instead of manually editing the Cloud Run manifest first.
12. Use `STAGING_GCP_ROLLOUT_RUNBOOK.md` for the exact command-by-command staging rollout order.

## MT5 Execution Constraint

1. The current code is safe for owner-scoped profile storage.
2. For personal testing, keep `MT5_EXECUTION_MODE=direct` on one Windows host.
3. For multi-user development, switch to `MT5_EXECUTION_MODE=queue` and run `python -m app.mt5_worker` on the Windows execution host.
4. Queue mode avoids rewriting the trading service later, but true multi-account live execution still needs one or more dedicated Windows workers per isolated execution context.