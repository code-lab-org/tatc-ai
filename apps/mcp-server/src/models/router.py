from .base import LLMClient
from .gemini import GeminiClient


def default_client() -> LLMClient:
    return GeminiClient()
