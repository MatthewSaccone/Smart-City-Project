# Smart City Platform

Starter monorepo for the Smart City microservices project.

## Structure
- `frontend/`: dashboard placeholder (choose framework as a team)
- `service-registry/`: planned registration, discovery, and health tracking
- `services/`: seven independent FastAPI starter services
- `shared/`: shared schemas and API contracts
- `docs/`: architecture, requirements, API, and meeting notes
- `infrastructure/`: future Terraform and Cloud Build configuration
- `.github/workflows/`: basic repository checks

## Services
1. `live-traffic` — Live Traffic Analytics
2. `traffic-predictor` — Traffic Predictor
3. `construction-incidents` — Construction & Incident Analytics
4. `parking-analytics` — Parking Analytics
5. `weather-analytics` — Weather & Air Quality Analytics
6. `transit-analytics` — Public Transit Analytics
7. `gasoline-analytics` — Gasoline Price Analytics

## Run one service locally
Requires Python 3.12+.

```powershell
cd services/live-traffic
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```
Visit `http://localhost:8080/health`.

## Docker
From the repository root:
```bash
docker build -t live-traffic:local ./services/live-traffic
docker run --rm -p 8080:8080 live-traffic:local
```

## GitHub
Create an empty GitHub repository, then run from this folder:
```bash
git init
git branch -M main
git add .
git commit -m "Initial Smart City platform structure"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

## Google Cloud Run deployment outline
Create/select a Google Cloud project, enable billing, install the Google Cloud CLI, and authenticate with `gcloud auth login`. Then (PowerShell; replace project ID):
```powershell
$PROJECT_ID = "YOUR_PROJECT_ID"
$REGION = "northamerica-northeast2"
$REPOSITORY = "smart-city"
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create $REPOSITORY --repository-format=docker --location=$REGION
gcloud auth configure-docker "$REGION-docker.pkg.dev"
docker build -t "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/live-traffic:latest" ./services/live-traffic
docker push "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/live-traffic:latest"
gcloud run deploy live-traffic --image "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/live-traffic:latest" --region $REGION --port 8080
```

Cloud Run gives each deployed service a URL; it does **not** implement the project's application-level Service Registry. That component still needs registration, discovery, and health-check logic.

## Security
Never commit API keys, passwords, `.env` files, or service-account keys. Use Secret Manager for secrets and least-privilege IAM. Decide which services are public/private before deployment.
