"""PKCE (RFC 7636), S256 method only."""

import base64
import hashlib
import secrets


def generate_verifier() -> str:
    """43-128 URL-safe characters; token_urlsafe(64) yields 86."""
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
