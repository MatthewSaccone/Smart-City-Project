# S2 — Traffic Predictor (local setup)

S2 is a FastAPI service backed by its **own PostgreSQL database**. It imports the City of Toronto *Midblock Traffic Speed* dataset and offers historical traffic analysis and experimental statistical predictions.

Commands below use **Windows PowerShell**, starting from the **repository root**. Requires Python 3.13, Docker Desktop, and the two source files `svc_summary_data.csv` and `svc_raw_data_speed_2025_2029.csv` (download separately; **do not commit the raw data**).

## 1. Python and PostgreSQL

```powershell
# First-time Python setup (skip if .venv already exists)
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r services/traffic-predictor/requirements.txt
python -m pip install pytest  # needed only to run tests

# Set the SAME password used when this Docker volume was first created
$env:S2_DB_PASSWORD = 'YOUR_LOCAL_DB_PASSWORD'
docker compose -f docker-compose.s2-db.yml up -d
$env:S2_DATABASE_URL = "postgresql://traffic_predictor:$($env:S2_DB_PASSWORD)@127.0.0.1:55432/traffic_predictor"
```

On Windows, if PowerShell blocks venv activation, run `Set-ExecutionPolicy -Scope Process Bypass` and retry. URL-encode special characters in the password when constructing `S2_DATABASE_URL`. **Never commit credentials.** Set the database URL separately in each new terminal.

## 2. Import historical data (first setup only)

```powershell
python services/traffic-predictor/scripts/import_toronto_midblock.py `
    --summary "C:\data\svc_summary_data.csv" `
    --speed "C:\data\svc_raw_data_speed_2025_2029.csv" `
    --source-timezone America/Toronto --strict

# Confirm observations were imported
docker compose -f docker-compose.s2-db.yml exec s2-postgres `
    psql -U traffic_predictor -d traffic_predictor `
    -c "SELECT COUNT(*) FROM speed_observations;"
```

Replace the `C:\data\...` examples with your CSV locations. Importing automatically creates the `001` historical schema; re-importing the same observation IDs does not create duplicates. The source timezone is a working assumption. If `--strict` rejects problematic source timestamps, inspect those records rather than silently discarding them.

## 3. Build the candidate prediction model (first setup / rebuild)

```powershell
python services/traffic-predictor/scripts/build_s2_baseline.py `
    --model-version toronto-p75-candidate-v1 `
    --cutoff 2026-08-01T00:00:00Z `
    --validation-end 2026-10-01T00:00:00Z `
    --output s2-p75-validation.json
```

The builder automatically applies `sql/002_baseline_model.sql` and `sql/003_baseline_p75.sql`; **do not run them separately** for normal setup. This version trains on earlier observations and writes a validation report. It remains **unapproved**: the exploratory macro-F1 is below the T026 target, and methodology review is pending.

## 4. Start the API (local development only)

In the **server terminal**, ensure `S2_DATABASE_URL` is set (Step 1), then:

```powershell
cd services/traffic-predictor
$env:PYTHONPATH = (Resolve-Path ..).Path
$env:S2_MODEL_VERSION = 'toronto-p75-candidate-v1'
$env:APP_ENV = 'development'
$env:S2_EXPERIMENTAL_PREVIEW = '1'  # local testing ONLY
Remove-Item Env:S2_HISTORY_CSV -ErrorAction SilentlyContinue
Remove-Item Env:S2_HISTORY_METADATA -ErrorAction SilentlyContinue
python -m uvicorn app.main:app --host 127.0.0.1 --port 8082
```

Swagger UI: **http://127.0.0.1:8082/docs**. Endpoints:

- `GET /v1/traffic/history/patterns` — historical patterns (T021)
- `POST /v1/traffic/predictions` — point forecast (T023)
- `GET /v1/traffic/predictions/high-congestion-periods` — high-period forecasts (T025)
- `GET /v1/traffic/predictions/areas` — area forecasts (T027)

**Do not enable `S2_EXPERIMENTAL_PREVIEW` in production or mark the model approved simply to avoid HTTP 503.** Production deployment, secrets, and Cloud SQL configuration are handled separately.

## 5. Tests and local benchmarks

Open another terminal in `services/traffic-predictor`, with `.venv` activated:

```powershell
$env:PYTHONPATH = (Resolve-Path ..).Path
python -m pytest tests -q

# Keep the API running in the other terminal; requires the real PostgreSQL backend
python scripts/benchmark_s2.py --scenario history --concurrency 25 --requests 100
python scripts/benchmark_s2.py --scenario point --concurrency 25 --requests 100
python scripts/benchmark_s2.py --scenario high-periods --concurrency 25 --requests 100
python scripts/benchmark_s2.py --scenario areas --concurrency 25 --requests 100
```

All four benchmarks passed locally with 25 concurrent workers and response times below 3 seconds on the original developer's machine. **Repeat after cloud deployment.** Don't commit generated `s2-*.json` evaluation/benchmark reports, `.venv`, secrets, or the raw CSVs.
