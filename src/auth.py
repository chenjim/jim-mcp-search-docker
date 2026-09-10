"""Static bearer-token authentication for the HTTP API and the MCP mount.

The service is meant to be reachable from other machines, so every functional
surface is protected by a single shared token:

- ``Authorization: Bearer <token>`` (preferred), or
- ``X-API-Key: <token>``

``/health`` stays open for container healthchecks. When ``JIM_SEARCH_AUTH_TOKEN``
is empty, auth is disabled (convenient for local-only use).
"""
import json
import logging
import secrets
from typing import Optional

from src import config

logger = logging.getLogger(__name__)

# Paths that require a valid token (prefix match).
PROTECTED_PREFIXES = ("/mcp", "/search", "/scrape")


def _extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return None


def is_authorized(authorization: Optional[str], x_api_key: Optional[str]) -> bool:
    """Return True when auth is disabled or the presented token matches."""
    expected = config.AUTH_TOKEN
    if not expected:
        return True
    provided = _extract_token(authorization, x_api_key)
    return bool(provided) and secrets.compare_digest(provided, expected)


async def _send_unauthorized(send) -> None:
    body = json.dumps({"detail": "Unauthorized"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    """Pure ASGI middleware (streaming-safe) guarding the protected prefixes."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and config.AUTH_TOKEN:
            path = scope.get("path", "")
            if path.startswith(PROTECTED_PREFIXES):
                headers = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in scope.get("headers", [])
                }
                if not is_authorized(headers.get("authorization"), headers.get("x-api-key")):
                    logger.warning("Rejected unauthorized request: %s %s", scope.get("method"), path)
                    await _send_unauthorized(send)
                    return
        await self.app(scope, receive, send)
