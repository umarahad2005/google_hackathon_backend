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
import os

from google import genai

from app.settings import get_settings

logger = logging.getLogger(__name__)

_client = None  # cached genai.Client (auth is constant per process)


def get_genai_client():
    """
    Build (once) and return a genai.Client in the configured auth mode.

    Auth modes (priority order when `gemini_use_vertex=True`):
    1. Vertex Express: `vertex_express_api_key` set → Vertex via that key.
       No service account / IAM / API-enable needed; billed via Express.
    2. Vertex (service account): ADC / GOOGLE_APPLICATION_CREDENTIALS +
       project/location → billed to the GCP project.
    Otherwise: plain AI Studio API key (free tier).
    """
    global _client
    if _client is not None:
        return _client

    s = get_settings()
    if s.gemini_use_vertex:
        # 1. Vertex AI Express mode — API key, no service account.
        if s.vertex_express_api_key:
            logger.info("Gemini auth: Vertex AI Express (API key)")
            _client = genai.Client(
                vertexai=True,
                api_key=s.vertex_express_api_key,
            )
            return _client

        # 2. Vertex AI with service-account / ADC.
        if (
            s.google_application_credentials
            and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        ):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = (
                s.google_application_credentials
            )
        project = s.google_cloud_project or os.environ.get(
            "GOOGLE_CLOUD_PROJECT", ""
        )
        if not project:
            raise RuntimeError(
                "gemini_use_vertex=True but neither vertex_express_api_key "
                "nor google_cloud_project is set."
            )
        logger.info(
            "Gemini auth: Vertex AI (project=%s, location=%s)",
            project, s.google_cloud_location,
        )
        _client = genai.Client(
            vertexai=True,
            project=project,
            location=s.google_cloud_location,
        )
    else:
        if not s.gemini_api_key:
            raise RuntimeError(
                "gemini_api_key is empty and gemini_use_vertex is False — "
                "no usable Gemini auth configured."
            )
        logger.info("Gemini auth: API key (AI Studio / free tier)")
        _client = genai.Client(api_key=s.gemini_api_key)

    return _client

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
