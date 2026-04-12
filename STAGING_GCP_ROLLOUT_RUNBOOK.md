# Staging GCP Rollout Runbook

This runbook is the concrete execution order for a Beacon staging deployment using:

- Cloud Run for the API tier
- Cloud SQL PostgreSQL for the shared database
- Secret Manager for sensitive settings
- A separate Windows MT5 worker host

## 1. Prerequisites

You need:

- Google Cloud SDK authenticated to the target project
- Artifact Registry API enabled
- Cloud Build API enabled
- Cloud Run API enabled
- Secret Manager API enabled
- Cloud SQL Admin API enabled
- A PostgreSQL Cloud SQL instance already created
- A Windows host prepared for the MT5 worker

Set your target project first:

```bash
gcloud config set project YOUR_PROJECT_ID
```

## 2. Create Artifact Registry

```bash
gcloud artifacts repositories create beacon \
  --repository-format=docker \
  --location=us-central1 \
  --description="Beacon container images"
```

Skip this if the repository already exists.

## 3. Create Secrets

```bash
gcloud secrets create beacon-database-url --replication-policy=automatic
gcloud secrets create beacon-control-api-key --replication-policy=automatic
gcloud secrets create beacon-mt5-profile-encryption-key --replication-policy=automatic
```

Populate them:

```bash
printf '%s' 'postgresql+psycopg://beacon_user:change_me@YOUR_CLOUD_SQL_HOST:5432/beacon' | gcloud secrets versions add beacon-database-url --data-file=-
printf '%s' 'replace-with-random-control-api-key' | gcloud secrets versions add beacon-control-api-key --data-file=-
printf '%s' 'replace-with-fernet-key' | gcloud secrets versions add beacon-mt5-profile-encryption-key --data-file=-
```

## 4. Review Staging API Config

Use [.env](.env) as the staging API baseline and [.env.gcp.api.example](.env.gcp.api.example) as the reference template.

Confirm the API tier remains Linux-safe:

- `MARKET_DATA_PROVIDER=auto`
- `EXECUTION_PROVIDER=MT5`
- `MT5_EXECUTION_MODE=queue`
- `DRY_RUN=true`
- `LIVE_TRADING_ARMED=false`
- `AUTO_TRADING_ENABLED=false`
- no MT5 terminal path or MT5 account credentials on Cloud Run

## 5. Deploy the API Tier

PowerShell path:

```powershell
./deploy_Beacon_GCP_Staging.ps1 `
  -ProjectId YOUR_PROJECT_ID `
  -CloudSqlInstance YOUR_CLOUDSQL_INSTANCE `
  -TrustedHosts "YOUR_RUN_APP_HOST,api.example.com" `
  -CorsAllowedOrigins "https://app.example.com" `
  -Region us-central1 `
  -Repository beacon `
  -ServiceName beacon-api `
  -CreateArtifactRegistryIfMissing
```

Manual Cloud Build path:

```bash
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions _REGION=us-central1,_REPOSITORY=beacon,_SERVICE=beacon-api
```

## 6. Verify API Health

Once deployed, verify:

```bash
curl https://YOUR_RUN_APP_HOST/health
```

Expected response:

```json
{"status":"ok"}
```

If operator access is configured for your current IP and control key, also verify metrics and dashboard access.

## 7. Prepare the Windows MT5 Worker

Use [.env.worker.example](.env.worker.example) as the starting point.

Create one private worker file per isolated execution owner such as `.env.worker.trader-alpha` and set:

- `DATABASE_URL` to the same Cloud SQL PostgreSQL database
- `MT5_TERMINAL_PATH` to the Windows terminal path
- `MT5_RUNTIME_OWNER_ID` to the owner served by that worker
- `MT5_WORKER_KEY` to the provisioned worker key for that owner
- `MT5_WORKER_LABEL` to a human-readable Windows host label
- `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` to the staging broker account
- `MT5_EXECUTION_MODE=queue`
- `DRY_RUN=false`
- `AUTO_TRADING_ENABLED=false`
- `LIVE_TRADING_ARMED=false`

Then follow [WINDOWS_MT5_WORKER_DEPLOYMENT.md](WINDOWS_MT5_WORKER_DEPLOYMENT.md).

Before starting any worker, provision the owner-bound worker keys from an operator workstation:

```powershell
./provision_Beacon_MT5_Workers.ps1 \
  -ApiBaseUrl https://YOUR_RUN_APP_HOST \
  -ConfigFile .\managed_workers.staging.example.json \
  -ControlApiKey YOUR_CONTROL_API_KEY
```

## 8. Start the Worker

On the Windows host, start one process per isolated owner:

```powershell
./start_MT5_Worker.ps1 \
  -EnvFile C:\Path\To\.env.worker.trader-alpha \
  -OwnerId trader-alpha \
  -WorkerKey worker-trader-alpha-1 \
  -WorkerLabel "Trader Alpha Windows Worker" \
  -RunMigrations
```

Start the second owner in a separate terminal or service wrapper:

```powershell
./start_MT5_Worker.ps1 \
  -EnvFile C:\Path\To\.env.worker.trader-beta \
  -OwnerId trader-beta \
  -WorkerKey worker-trader-beta-1 \
  -WorkerLabel "Trader Beta Windows Worker"
```

## 9. Validate Queue Processing

From the API tier or dashboard:

1. Confirm `GET /api/mt5/workers?owner_id=trader-alpha` and `GET /api/mt5/workers?owner_id=trader-beta` each show the expected `worker_key` and `profile_id`.
2. Trigger one non-live workflow for `trader-alpha` and one for `trader-beta` that creates execution requests.
3. Confirm rows appear in `mt5_execution_jobs` with the correct `owner_id`, `profile_id`, and `assigned_worker_key`.
4. Confirm each worker only claims its own jobs and processes them to `FILLED` or `FAILED`.
5. Confirm results propagate back to the dashboard and journals.
6. Run the deterministic local isolation verifier before or after staging rollout when you want a fast sanity check on the queue model:

```powershell
.\.venv\Scripts\python.exe smoke_test_managed_workers.py
```

## 10. Staging Safety Gate

Do not change these until end-to-end staging is proven:

- keep Cloud Run API in queue mode
- keep `LIVE_TRADING_ARMED=false`
- keep `AUTO_TRADING_ENABLED=false`
- keep MT5 credentials only on the Windows worker

## 11. Promotion Readiness

Before any move toward production:

1. Validate Cloud SQL connectivity and migrations.
2. Validate Secret Manager values are correct.
3. Validate one queued MT5 execution cycle for each isolated owner.
4. Validate dashboard, health, metrics, and worker-list endpoints.
5. Validate Windows workers survive restart and reconnect to MT5 with the same `MT5_WORKER_KEY` identity.
