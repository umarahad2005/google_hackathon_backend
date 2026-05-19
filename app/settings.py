"""
Zimma AI — Application Settings (pydantic-settings).

All configuration is loaded from environment variables / .env file.
No secret literals allowed in code — this is enforced by the security checklist.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Gemini ---
    # Plain AI Studio API key (generativelanguage.googleapis.com, free tier).
    # Optional when running in Vertex mode (org policy may block plain keys).
    gemini_api_key: str = ""

    # --- Vertex AI mode ---
    # When true, calls go through Vertex AI (aiplatform.googleapis.com),
    # authenticated by a service account → billed to the GCP project
    # (uses your credit, not the free-tier limit:0). Required if your org
    # policy forbids plain-API-key access to Gemini.
    gemini_use_vertex: bool = False
    # Vertex AI Express Mode key (starts with "AQ."). If set (and
    # gemini_use_vertex=True), used instead of service-account auth — no
    # IAM role / API-enable / JSON needed.
    vertex_express_api_key: str = ""
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    # Preferred for cloud deploys (FastAPI Cloud etc.): the FULL contents
    # of the service-account JSON as one env var / secret. Used before the
    # file path below — no file needs to exist on disk.
    google_service_account_json: str = ""
    # Local-dev path to the service-account JSON. If empty, Application
    # Default Credentials (GOOGLE_APPLICATION_CREDENTIALS / metadata) used.
    google_application_credentials: str = ""

    # --- Google Maps ---
    google_maps_api_key: str

    # --- Supabase ---
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str

    # --- App ---
    app_env: str = "development"
    demo_clock_multiplier: int = 60  # 1 real sec = N sim minutes
    # Demo-video compression: when > 0, follow-ups fire on a fixed,
    # evenly-spaced cadence of N real seconds per step (step k fires at
    # (k+1)*N s), so the whole reminder→completion→rating lifecycle plays
    # out in one smooth take. Set 0 to use the real compressed timeline.
    followup_demo_gap_s: float = 5.0
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    # --- Model config ---
    # On Vertex (project zimmaai / us-central1) only the 2.5 family is
    # available; 2.0 / 1.5 models return 404. Primaries are set to
    # known-good Vertex models so no call is wasted on a 404 fallthrough.
    gemini_flash_model: str = "gemini-2.5-flash"
    gemini_pro_model: str = "gemini-2.5-pro"

    # Ordered fallback chains (comma-separated). The primary model above is
    # tried first; if it is quota-exhausted / rate-limited, the next model
    # in the chain is tried automatically. Override via env if needed.
    gemini_flash_fallbacks: str = "gemini-2.5-flash-lite,gemini-2.5-pro"
    gemini_pro_fallbacks: str = "gemini-2.5-flash,gemini-2.5-flash-lite"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
