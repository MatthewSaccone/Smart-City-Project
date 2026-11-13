"""S4 trusted JWT identity and internal-estimator permission checks."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from shared.auth import JWTSettings, TokenVerifier, authenticate, Principal

bearer = HTTPBearer(auto_error=False, scheme_name="ServiceBearer")


def configure_auth(app):
    app.state.token_verifier = TokenVerifier(JWTSettings.from_env())


def get_current_principal(request: Request,
                          credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    return authenticate(request, credentials)


def require_estimator(principal: Principal = Depends(get_current_principal)):
    if not principal.has_permission("parking:estimate"):
        raise HTTPException(403, "Internal client lacks parking:estimate permission")
    return principal
