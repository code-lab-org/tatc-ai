import os
import time
from typing import Optional

from .base import LLMResponse


class GeminiClient:
    def __init__(self, model: Optional[str] = None):
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def generate(self, prompt: str, system: Optional[str] = None) -> LLMResponse:
        from google.genai import types

        config = types.GenerateContentConfig(system_instruction=system) if system else None
        started = time.perf_counter()
        response = self._client.models.generate_content(
            model=self.model, contents=prompt, config=config
        )
        latency = time.perf_counter() - started
        meta = response.usage_metadata
        return LLMResponse(
            text=response.text or "",
            model=self.model,
            input_tokens=(meta.prompt_token_count or 0) if meta else 0,
            output_tokens=(meta.candidates_token_count or 0) if meta else 0,
            latency_s=latency,
        )
