# S2 authentication integration

S2 now validates access tokens from the same Keycloak issuer, API audience and
signing-key endpoint as S3. All four `/v1/traffic/...` analytics operations require
`Authorization: Bearer <access_token>`. `/health` and `/ready` remain accessible
operational probes. Swagger provides an **Authorize** button.

S2 performs read-only analytics, so any verified identity for its configured
audience can call these operations. No new moderator or ownership restriction is
introduced. Existing dataset availability, model approval and experimental-preview
checks still apply after authentication. An authenticated request can therefore
receive 503 when there is no eligible model; authentication does not approve a model.

This changes anonymous access. The updated [S2 contract](../../docs/api/contracts/S2-Traffic-Predictor.md)
and OpenAPI document record a version 1.1 migration for team/client review before
deployment. Callers must start sending tokens before the protected S2 is rolled out.
S1, S3, S4 and T009 are unchanged.

## Configure the resource server

| Setting | Meaning |
|---|---|
| `JWT_ISSUER` | Exact signed `iss`, normally `http://localhost:8088/realms/smart-city` locally |
| `JWT_AUDIENCE` | Expected `aud`, normally `smart-city-api`, as in S3 |
| `JWT_JWKS_URL` | Trusted public signing keys; server configuration, never a token-controlled URL |
| `JWT_ROLE_CLAIM` | Signed role claim, defaults to `roles`; no elevated role is required for S2 analytics |
| `JWT_LEEWAY_SECONDS` | Clock tolerance 0–60 seconds, default 30 |
| `JWT_JWKS_TIMEOUT_SECONDS` | Signing-key request timeout 1–10 seconds, default 5 |
| `JWT_ALLOW_INSECURE_JWKS` | Allows internal HTTP to `keycloak:8080` only with `APP_ENV=development` |

Set issuer, audience and JWKS together. With all three unset, the process can serve
its probes but protected requests fail closed. Partial/unsafe settings fail startup.
As in S3, localhost HTTP is supported for a host-running development server; other
JWKS endpoints require HTTPS except explicitly enabled development Keycloak.

The root Compose file passes these settings to **S2 and S3**, using `.env` values.
It does not create or change your team's realm, users or clients. Start the existing
Keycloak separately when needed (`docker compose up -d keycloak`); its initial realm
and client configuration must already be provisioned. S2 does not depend on Keycloak
startup for liveness and does not silently skip token checks if it is unavailable.

For a host-running S2, keep the database/history/model setup in [S2_README](S2_README.md).
Then, in the server terminal at the repository root:

```powershell
$env:PYTHONPATH = (Resolve-Path .\services).Path
$env:JWT_ISSUER = 'http://localhost:8088/realms/smart-city'
$env:JWT_AUDIENCE = 'smart-city-api'
$env:JWT_JWKS_URL = 'http://localhost:8088/realms/smart-city/protocol/openid-connect/certs'
$env:REGISTRY_URL = '' # standalone host run only
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services/traffic-predictor --host 127.0.0.1 --port 8082
```

Within Compose, the JWKS URL instead uses
`http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs` and the two
development settings above. The issuer is still the exact issuer configured in
Keycloak, not the internal Docker hostname. A normal deployment requires a reachable
trusted provider and HTTPS configuration.

## Calling S2 from another service

Use an existing, approved confidential Keycloak client with **Client authentication**
and **Service accounts roles** enabled. Configure an Audience protocol mapper to
include `smart-city-api` in access tokens. Keep client secrets on the calling server;
S2 needs only public signing keys. No new service database credentials or registry
API key are involved in access-token validation.

In a caller terminal at the repository root:

```powershell
$env:S2_CLIENT_ID = 'YOUR_APPROVED_SERVICE_CLIENT_ID'
$clientSecret = Read-Host 'Service client secret' -AsSecureString
$credential = New-Object System.Management.Automation.PSCredential($env:S2_CLIENT_ID, $clientSecret)
$tokenResponse = Invoke-RestMethod -Method Post -Uri 'http://localhost:8088/realms/smart-city/protocol/openid-connect/token' -ContentType 'application/x-www-form-urlencoded' -Body @{
    grant_type = 'client_credentials'
    client_id = $env:S2_CLIENT_ID
    client_secret = $credential.GetNetworkCredential().Password
}
$env:S2_ACCESS_TOKEN = $tokenResponse.access_token
$credential = $null
$clientSecret = $null
$headers = @{ Authorization = "Bearer $env:S2_ACCESS_TOKEN" }

Invoke-RestMethod 'http://localhost:8082/v1/traffic/history/patterns?latitude=43.0096&longitude=-81.2737&timezone=America%2FToronto' -Headers $headers
```

Cache the caller's token only until its expiry and request another token as needed.
For a user-facing frontend, use the team's approved user login flow; never embed a
confidential service-account secret in browser code. S2 accepts already issued user
tokens for the configured audience as well as service-account tokens.

Missing/invalid tokens return standard `401 AUTHENTICATION_REQUIRED` with a bearer
challenge. A required signing-key fetch that fails returns retryable
`503 SERVICE_UNAVAILABLE`. Verified identities come only from signed claims; request
body IDs, `X-User-ID`, `X-Roles` and registry API keys cannot establish an identity.
Errors and ordinary logs do not expose tokens, signing-key URLs or provider details.

The JWKS cache lasts five minutes; individual signing keys are not cached indefinitely.
Previously cached keys can support requests during that interval of an outage.
Once the cache expires, failed retrieval denies access. Token signature, issuer,
audience, expiry, issuance time and optional not-before are checked on every request.
JWT verification does not provide immediate per-token logout/revocation introspection.

Implementation references: [Keycloak service accounts and audience configuration](https://www.keycloak.org/docs/latest/server_admin/index.html),
[PyJWT verification and signing-key caching](https://pyjwt.readthedocs.io/en/stable/api.html).

## Reproduce verification

Verified November 12, 2026 (America/Toronto), from merged main `7e3b9a2` on
`traffic-predictor-auth-aaliyan`:

- S2: **107 tests passed**, including real RSA signatures, HTTP JWKS retrieval,
  required claims, invalid credentials, all protected routes, key rotation and outages.
- Unchanged S4: **74 regression tests passed**.
- Docker/Keycloak: actual client-credentials token accepted, anonymous request rejected,
  service registration preserved, and cached/uncached outage behavior verified.
- Authenticated fixture history: **75 requests with 25 client workers**, zero errors,
  p95 **138.966 ms**, maximum **150.700 ms**. These are local history measurements,
  not prediction-model performance or production scalability evidence.

The machine-readable [authentication report](auth-verification.json) contains no
credentials. The temporary integration project's containers and volumes were removed.

From the repository root (run each service's tests separately because they use the
same Python package name `app`):

```powershell
.\.venv\Scripts\python.exe -m pip install -r services/traffic-predictor/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -c services/traffic-predictor/pytest.ini services/traffic-predictor/tests -q
.\.venv\Scripts\python.exe services/traffic-predictor/scripts/verify_auth_compose.py
```

The integration script creates its own randomly named Compose project and temporary
client/realm credentials. It builds the actual S2 Dockerfile, uses the team's Keycloak
26.4.7 image and registry, and mounts clearly labeled historical fixtures. It checks
anonymous rejection, a real client-credentials token, S2 registration, authenticated
history requests, cached keys during an outage, and 503 after restarting only its S2
container without cached keys. It removes its own containers and volumes. Existing
running team containers, identities and databases are not altered.

For existing real-database latency scripts, set `S2_ACCESS_TOKEN` in the benchmark
terminal before running the commands from S2_README. `--token-env` selects a different
environment variable. Tokens are not accepted as command-line arguments or included
in benchmark output. Previous prediction measurements predate this authorization
change; repeat them with the appropriate approved model/data and deployment.
