import os
import time
from typing import Optional

from .base import LLMResponse, LLMTurn, ToolCall


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

    def converse(
        self, messages: list[dict], tools: list[dict], system: Optional[str] = None
    ) -> LLMTurn:
        from google.genai import types

        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m["text"])]))
            elif m["role"] == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(name=c.name, args=c.arguments)
                            )
                            for c in m["tool_calls"]
                        ],
                    )
                )
            elif m["role"] == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=m["name"], response={"result": m["result"]}
                                )
                            )
                        ],
                    )
                )
        declarations = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t["parameters"],
            )
            for t in tools
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)],
        )
        started = time.perf_counter()
        response = self._client.models.generate_content(
            model=self.model, contents=contents, config=config
        )
        latency = time.perf_counter() - started
        text = None
        tool_calls = []
        for part in response.candidates[0].content.parts:
            call = getattr(part, "function_call", None)
            if call:
                tool_calls.append(ToolCall(name=call.name, arguments=dict(call.args or {})))
            elif getattr(part, "text", None):
                text = (text or "") + part.text
        meta = response.usage_metadata
        return LLMTurn(
            model=self.model,
            input_tokens=(meta.prompt_token_count or 0) if meta else 0,
            output_tokens=(meta.candidates_token_count or 0) if meta else 0,
            latency_s=latency,
            text=text,
            tool_calls=tool_calls,
        )
