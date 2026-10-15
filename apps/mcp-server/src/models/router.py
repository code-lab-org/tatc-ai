from typing import Optional

from .base import LLMClient
from .gemini import GeminiClient


def default_client() -> LLMClient:
    return GeminiClient()


def client_for(model: Optional[str] = None) -> LLMClient:
    return GeminiClient(model=model)
