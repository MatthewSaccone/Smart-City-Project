"""S4 trusted identity and internal estimator authorization."""
from fastapi import Depends, HTTPException
from shared.auth import JWTSettings, TokenVerifier, Principal, get_current_principal

def configure_auth(app):
    app.state.token_verifier = TokenVerifier(JWTSettings.from_env())

def require_estimator(principal: Principal = Depends(get_current_principal)):
    if "service" not in principal.roles or not principal.has_permission("parking:estimate"):
        raise HTTPException(403, "Internal client lacks parking:estimate permission")
    return principal
