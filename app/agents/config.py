"""
Zimma AI — Agent Model Configuration.

Centralized model IDs per skills/gemini-adk-agent.md:
- Routing/extraction → gemini-2.0-flash
- Reasoning → gemini-2.x-pro
"""

from app.settings import get_settings


class ModelConfig:
    """Centralized model selection — change here, changes everywhere."""

    @property
    def flash(self) -> str:
        """Fast model for routing, intent extraction, tool selection."""
        return get_settings().gemini_flash_model

    @property
    def pro(self) -> str:
        """Pro model for ranking reasoning, follow-up drafting."""
        return get_settings().gemini_pro_model


MODELS = ModelConfig()

# Ranking score weights (tunable, documented in README)
RANKING_WEIGHTS = {
    "distance": 0.40,
    "availability": 0.25,
    "rating": 0.25,
    "price_fit": 0.10,
}
