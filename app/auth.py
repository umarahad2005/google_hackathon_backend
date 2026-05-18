"""
Zimma AI — Request authentication.

Validates the Supabase access token (Bearer JWT) sent by the Flutter app
and resolves the authenticated user id. Validation is delegated to Supabase
(`auth.get_user`) so we don't have to manage the JWT secret here.

The dependency is intentionally *optional*: if no/invalid token is present
it returns None and callers fall back to the demo user. This keeps the
deployed demo working during the auth rollout (before RLS is applied and
before every client ships with a session). Tighten to a hard 401 once all
clients authenticate.

Owner: Backend Engineer (03)
"""

from __future__ import annotations

import logging

from fastapi import Header

from app.services.supabase import get_supabase

logger = logging.getLogger(__name__)


async def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> str | None:
    """Return the Supabase user id from the Bearer token, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None

    try:
        resp = get_supabase().auth.get_user(token)
        user = getattr(resp, "user", None)
        user_id = getattr(user, "id", None) if user is not None else None
        return str(user_id) if user_id else None
    except Exception as e:  # noqa: BLE001 — never let auth errors 500
        logger.warning("Token validation failed: %s", e)
        return None
