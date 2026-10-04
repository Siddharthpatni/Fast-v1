"""LLM services: structured disruption extraction with a rule-based fallback.

Providers: OpenRouter, Ollama or OpenAI, alone or as a fallback chain.
"""

from app.services.llm.client import (
    FallbackLLMClient,
    LLMClient,
    LLMError,
    QuotaExhaustedError,
    make_llm_client,
)
from app.services.llm.extraction import (
    DisruptionExtraction,
    extract_disruption,
    extract_with_rules,
)

__all__ = [
    "DisruptionExtraction",
    "FallbackLLMClient",
    "LLMClient",
    "LLMError",
    "QuotaExhaustedError",
    "extract_disruption",
    "extract_with_rules",
    "make_llm_client",
]
