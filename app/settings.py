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
    gemini_api_key: str

    # --- Google Maps ---
    google_maps_api_key: str

    # --- Supabase ---
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str

    # --- App ---
    app_env: str = "development"
    demo_clock_multiplier: int = 60  # 1 real sec = N sim minutes
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    # --- Model config ---
    # NOTE: gemini-2.5-pro has a very low / billing-gated free-tier quota
    # and is the usual cause of "quota exceeded". 2.5-flash has a generous
    # free tier and is more than enough for short ranking/reasoning prose.
    gemini_flash_model: str = "gemini-2.0-flash"
    gemini_pro_model: str = "gemini-2.5-flash"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
