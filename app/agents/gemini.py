"""
Zimma AI — Resilient Gemini call helper.

Wraps `client.models.generate_content` with an automatic model-fallback
chain: if a model is rate-limited / quota-exhausted / temporarily
unavailable, the next model in the chain is tried before giving up.

This is the single place model fallback is implemented — intent and
ranking agents both call through here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Substrings that indicate the error is worth retrying on the NEXT model
# (transient capacity / quota), as opposed to a hard request error (bad
# prompt, auth) where switching models would not help.
_RETRYABLE = (
    "quota",
    "exhausted",
    "resource_exhausted",
    "429",
    "rate limit",
    "rate-limit",
    "unavailable",
    "overloaded",
    "503",
    "500",
    "internal error",
    "deadline",
    "timeout",
)


def _is_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(s in msg for s in _RETRYABLE)


def generate_content_resilient(client, models, *, contents, config):
    """
    Try each model in `models` in order. On a retryable (quota/capacity)
    failure, fall back to the next model. Returns `(response, model_used)`.

    Raises the last exception if every model fails, or immediately if the
    failure is not retryable (e.g. malformed request / auth).
    """
    if not models:
        raise ValueError("generate_content_resilient: empty model list")

    last_err: Exception | None = None
    for i, model in enumerate(models):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            if i > 0:
                logger.info(
                    "Gemini fallback succeeded on '%s' (model #%d of %d)",
                    model, i + 1, len(models),
                )
            return response, model
        except Exception as e:  # noqa: BLE001 — classified below
            last_err = e
            is_last = i == len(models) - 1
            if not _is_retryable(e):
                logger.error(
                    "Gemini '%s' failed with non-retryable error: %s", model, e
                )
                raise
            if is_last:
                logger.error(
                    "Gemini exhausted all %d models; last error on '%s': %s",
                    len(models), model, e,
                )
                raise
            logger.warning(
                "Gemini '%s' rate-limited/unavailable (%s) — falling back to '%s'",
                model, str(e)[:120], models[i + 1],
            )

    # Unreachable, but keeps type-checkers happy.
    assert last_err is not None
    raise last_err
