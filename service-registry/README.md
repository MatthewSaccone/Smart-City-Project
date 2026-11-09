# Service Registry

The Service Registry provides logical discovery for backend service instances. It is intended to allow consumers to look up a service by logical ID rather than hard-coding instance endpoints.

> This README is a developer entry point. The detailed design, API contracts, implementation record, and acceptance evidence belong in [`docs/architecture/service-registry.md`](../docs/architecture/service-registry.md).

## Responsibilities

- Accept authorized instance registrations.
- Maintain logical service and instance records.
- Support discovery of eligible instances.
- Track instance heartbeats and lease expiry.
- Support multiple instances for one logical service.
- Implement the agreed persistence, replication, and failover strategy.

Only list capabilities as implemented after they exist in code and have been tested.

## Project Layout

```text
service-registry/
├── app/
├── tests/
├── Dockerfile
├── README.md
└── requirements.txt
```

## Local Setup

From the repository root, open PowerShell:

```powershell
cd service-registry
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, use the team's approved virtual-environment activation method or invoke the environment's Python executable directly.

## Run

The following command assumes the application entry point is `app.main:app` and listens on port 8080. Confirm this matches the implementation:

```powershell
uvicorn app.main:app --reload --port 8080
```

## Test

If pytest is configured:

```powershell
pytest -q
```

Record the actual output and test environment in the central registry documentation. A passing local test suite does not by itself prove replication or failover acceptance criteria.

## Configuration

Document required environment variables here once the implementation is settled. Never commit real secrets, populated `.env` files, credentials, or service-account keys. Keep safe examples in `.env.example`.

## API Documentation

If FastAPI's OpenAPI docs are enabled, the interactive API documentation is commonly available at:

```text
http://127.0.0.1:8080/docs
```

Confirm the configured port and route.

## Security and Deployment

Do not expose registration, heartbeat, or deregistration endpoints publicly without authentication and authorization.

Do not use local SQLite files as shared state across multiple deployed replicas.

Follow the central deployment and registry design documents.