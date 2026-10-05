"""Ollama provider.

Talks to a single local Ollama server over HTTP. Only one request is in flight
at a time by default (see the scheduler), which is what allows eight logical
agents to share one loaded model on a 16 GB machine.
"""

from __future__ import annotations

import time

import httpx

from ecosystem.config import get_settings
from ecosystem.services.llm.base import LLMRequest, LLMResponse


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.agent_model
        self.timeout = settings.llm_request_timeout

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "prompt": request.prompt,
            "system": request.system,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if request.json_mode:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()

        text = data.get("response", "")
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResponse(
            text=text,
            model=self.model,
            provider=self.name,
            latency_ms=latency_ms,
            tokens_estimate=len(text) // 4,
        )

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                models = [m.get("name") for m in response.json().get("models", [])]
            return {"ok": True, "provider": self.name, "models": models}
        except Exception as exc:
            return {"ok": False, "provider": self.name, "error": str(exc)}
