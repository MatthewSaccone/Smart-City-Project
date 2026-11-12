"""Configured internal credentials for the local demo; no platform issuer is invented."""

import json
import os
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer = HTTPBearer(auto_error=False, scheme_name="ServiceBearer")


def load_clients():
    clients = json.loads(os.getenv("PARKING_INTERNAL_CLIENTS", "[]"))
    if not isinstance(clients, list):
        raise ValueError("PARKING_INTERNAL_CLIENTS must be a list")
    for client in clients:
        if (not isinstance(client, dict) or not isinstance(client.get("token"), str)
                or len(client["token"]) < 24 or not client["token"].isascii()
                or not isinstance(client.get("permissions"), list)
                or not all(isinstance(permission, str) for permission in client["permissions"])):
            raise ValueError("Invalid internal client configuration")
        if client["token"] == os.getenv("REGISTRY_API_KEY"):
            raise ValueError("Estimator credential must differ from registry credential")
    return clients


def require_estimator(request: Request,
                      credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    supplied = credentials.credentials if credentials else ""
    if supplied and supplied.isascii():
        for client in request.app.state.internal_clients:
            if secrets.compare_digest(supplied, client["token"]):
                if "parking:estimate" not in client["permissions"]:
                    raise HTTPException(403, "Internal client lacks parking:estimate permission")
                return
    raise HTTPException(401, "Valid internal bearer credential required", headers={"WWW-Authenticate": "Bearer"})
