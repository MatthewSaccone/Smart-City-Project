# S4 Keycloak JWT authentication

S4 uses the same trusted RS256 verifier as S2, now located in
`services/shared/auth.py`. S2 retains its existing import interface and behavior.
S3's implementation is unchanged. Each application has its own verifier/key cache.

All four `/v1/parking/...` operations require a valid bearer JWT: facility search,
facility details, occupancy estimation and ingestion status. Estimation additionally
requires `parking:estimate` from signed claims. `/health` and `/ready` remain accessible
probes. Swagger exposes **Authorize**. API response models and occupancy calculations
are unchanged; the [version 1.1 contract](../../docs/api/contracts/S4-Parking-Analytics.md)
records the access migration for review with existing clients before deployment.

## Configure the receiving service

Use the same values as your team's S2/S3 Keycloak setup:

| Variable | Local example/default |
|---|---|
| `JWT_ISSUER` | `http://localhost:8088/realms/smart-city` — exact signed issuer |
| `JWT_AUDIENCE` | `smart-city-api` |
| `JWT_JWKS_URL` | Host process: `http://localhost:8088/realms/smart-city/protocol/openid-connect/certs` |
| `JWT_JWKS_URL` in Docker | `http://keycloak:8080/realms/smart-city/protocol/openid-connect/certs` |
| `JWT_ROLE_CLAIM` | `roles` |
| `JWT_LEEWAY_SECONDS` | `30`, bounded 0–60 |
| `JWT_JWKS_TIMEOUT_SECONDS` | `5`, bounded 1–10 |

Docker's internal HTTP Keycloak URL requires **both** `APP_ENV=development` and
`JWT_ALLOW_INSECURE_JWKS=true`, as in S3. Other non-local JWKS URLs require HTTPS.
The root Compose file passes these shared settings into S4. It does not change the
team's realm, clients, users or grants. The existing `.env.example` contains the common
Keycloak settings; add your own `PARKING_DB_PASSWORD` only if using the optional S4 DB.

The verifier fixes the signing algorithm to RS256 and checks the signature, issuer,
audience, expiry, issuance time, nonempty subject and optional not-before. It requires
`exp`, `iat`, `iss`, `aud`, `sub` and a signing-key ID. Token-controlled key-server URLs
and caller identity headers are not trusted. All issuer/audience/JWKS settings must
be supplied together. With no issuer configured, protected access fails closed.

`PARKING_INTERNAL_CLIENTS` opaque demo credentials are no longer accepted. There is
no application authentication bypass. The standalone benchmark creates a separate
ephemeral local signing-key server for its own temporary application process; it
still exercises the same JWT verification path and never configures your team realm.

## Calling S4 from a service

Use a confidential Keycloak client with **Client authentication** and **Service
accounts roles** enabled. Add an Audience protocol mapper including `smart-city-api`
in access tokens. Grant `parking:estimate` **only to approved internal service
accounts**, not ordinary frontend/user clients. Supported signed carriers are:

- `permissions`: a string or array containing `parking:estimate`;
- `scope`: a space-separated string containing `parking:estimate`;
- the configured `JWT_ROLE_CLAIM`, as a string/array with that exact role.

For example, a Hardcoded claim mapper on a dedicated internal client can place
`parking:estimate` in the `permissions` claim as type String and include it in access
tokens. This was verified with the actual Keycloak 26.4.7 image. Do not add that
mapper to a broadly available user client. Mapping/grant administration is owned by
the identity provider; S4 cannot infer service entitlement from request body IDs.

From a PowerShell caller terminal:

```powershell
$env:PARKING_CLIENT_ID = 'YOUR_APPROVED_INTERNAL_CLIENT_ID'
$secret = Read-Host 'Keycloak client secret' -AsSecureString
$credential = New-Object System.Management.Automation.PSCredential($env:PARKING_CLIENT_ID, $secret)
$tokenResponse = Invoke-RestMethod -Method Post -Uri 'http://localhost:8088/realms/smart-city/protocol/openid-connect/token' -ContentType 'application/x-www-form-urlencoded' -Body @{
    grant_type = 'client_credentials'
    client_id = $env:PARKING_CLIENT_ID
    client_secret = $credential.GetNetworkCredential().Password
}
$env:PARKING_ACCESS_TOKEN = $tokenResponse.access_token
$credential = $null
$secret = $null
$headers = @{ Authorization = "Bearer $env:PARKING_ACCESS_TOKEN" }
Invoke-RestMethod 'http://localhost:8084/v1/parking/facilities/demo-central' -Headers $headers
```

Cache the caller token only until expiry and obtain a replacement when required.
Frontend users should use the team's approved interactive login flow and send its
access token for reads; never embed a confidential client secret in browser code.

## Failures and verification

- Missing/invalid/expired credentials: `401 AUTHENTICATION_REQUIRED` and
  `WWW-Authenticate: Bearer`.
- Verified identity without estimator permission: `403 FORBIDDEN`.
- Required trusted signing keys unavailable/unconfigured: retryable
  `503 SERVICE_UNAVAILABLE`; retained parking data does not grant anonymous access.
- JWKS are cached for five minutes, without indefinite individual-key caching.
  Cached keys can work during that interval of an outage; signatures and time claims
  are still checked. After cache expiry, failed key retrieval denies access. This
  does not provide immediate per-token revocation introspection.

Tests exercise real RSA/JWKS validation, forged/expired/wrong-issuer/wrong-audience
tokens, all protected routes, permission carriers, spoofed headers and signing-key
outages. The [T010 verification record](T010.md) includes the real Keycloak,
PostgreSQL, registry and authenticated HTTP workload results.

References: [Keycloak protocol mappers](https://www.keycloak.org/admin-api/protocol-mappers),
[PyJWT verification/key caching](https://pyjwt.readthedocs.io/en/stable/api.html).
