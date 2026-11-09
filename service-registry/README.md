
# Service Registry

The Service Registry provides logical discovery for backend service instances. It allows consumers to look up services by logical ID rather than hard-coding instance endpoints.

Detailed design, API contracts, implementation records, and acceptance evidence are maintained in [the Service Registry architecture documentation](../docs/architecture/Service-Registry.md).

## Responsibilities

- Accept authenticated instance registrations.
- Maintain logical service and instance records.
- Support discovery of eligible instances.
- Track instance heartbeats and lease expiry.
- Support multiple instances for one logical service.
- Provide the foundation for shared persistence, replication, and failover.

## Project Layout

```text
service-registry/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── models.py
│   └── repository.py
├── data/
├── tests/
│   └── test_registry.py
├── Dockerfile
├── requirements.txt
└── README.md
```

## Local Setup

From the repository root, open PowerShell:

```powershell
cd service-registry

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

## Configure the Registry API Key

Generate a random API key:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set the generated key in the current PowerShell session:

```powershell
$env:REGISTRY_API_KEY = "PASTE_YOUR_GENERATED_KEY_HERE"
```

The key is required for registration, heartbeat, and deregistration operations.

Do not commit the actual key or place it in source code.

## Run

```powershell
uvicorn app.main:app --reload --port 8080
```

The API listens on port 8080.

## API Documentation

- Health: http://127.0.0.1:8080/health
- Readiness: http://127.0.0.1:8080/ready
- Interactive API documentation: http://127.0.0.1:8080/docs

## Tests

Run from the `service-registry` directory:

```powershell
python -m pytest tests -v
```

## Authentication

Management endpoints require the `X-Registry-API-Key` header.

Discovery and health endpoints do not currently require this key. Restrict access to the registry through the deployment network and gateway policies as appropriate.

## Storage and Deployment

SQLite is currently used for local development and single-process operation.

Do not use independent local SQLite files as shared state across multiple deployed replicas. Production deployment requires a shared persistent store and a separately designed and tested replication and failover strategy.

Never commit real secrets, populated `.env` files, credentials, or service-account keys.
