"""S2 compatibility imports for the shared S2/S4 JWT verifier."""

from shared.auth import (
    JWTSettings, Principal, TokenVerifier, IdentityProviderUnavailable,
    InvalidAccessToken, authenticate, get_current_principal,
)
