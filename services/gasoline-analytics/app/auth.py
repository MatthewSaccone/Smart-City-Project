"""JWT-only reporting permission; no development bypass or caller-supplied user ID."""
from fastapi import Depends, HTTPException
from shared.auth import JWTSettings, TokenVerifier, get_current_principal, Principal


def configure_auth(app):
    app.state.token_verifier = TokenVerifier(JWTSettings.from_env())


def require_reporter(principal: Principal = Depends(get_current_principal)):
    if not principal.has_permission('gasoline:report'):
        raise HTTPException(403, 'Verified identity lacks gasoline:report permission')
    return principal
