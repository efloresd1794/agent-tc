"""Cognito JWT validation for FastAPI."""

import logging
from functools import lru_cache

import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt
from jose.utils import base64url_decode

from app.config import settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer()


@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """Fetch and cache Cognito JWKS."""
    url = (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com"
        f"/{settings.cognito_user_pool_id}/.well-known/jwks.json"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return {k["kid"]: k for k in resp.json()["keys"]}


def _verify_token(token: str) -> dict:
    """Verify a Cognito JWT and return its claims."""
    try:
        headers = jwt.get_unverified_header(token)
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token header: {e}")

    kid = headers.get("kid")
    jwks = _get_jwks()

    if kid not in jwks:
        # Refresh cache once and retry
        _get_jwks.cache_clear()
        jwks = _get_jwks()

    if kid not in jwks:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown token key ID")

    public_key = jwk.construct(jwks[kid])

    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=settings.cognito_app_client_id,
            options={"verify_exp": True},
        )
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Token validation failed: {e}")

    return claims


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """FastAPI dependency — validates Bearer JWT and returns claims."""
    return _verify_token(credentials.credentials)
