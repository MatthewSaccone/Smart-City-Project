# traffic-predictor authentication

See [Authentik migration and configuration](../../docs/Authentik-Migration.md) for setup, claim mapping, token acquisition and verification. All business endpoints require a trusted access token with the `smart-city` scope and an application role or explicit service permissions. Health/readiness and root remain public. S4 estimation requires a machine identity and `parking:estimate`; S2 POST predictions are read operations. Older verification reports are historical only and are not evidence of the new deployment.
