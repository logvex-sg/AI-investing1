"""Inference scheduler.

Eight logical agents share one model. The scheduler is the component that
makes that safe on a 16 GB laptop:

* a semaphore bounds how many requests run at once (`llm_max_concurrency`);
* requests are queued in priority order, so the Overseer and the agents whose
  turn it is are served ahead of low-priority background analysis;
* results are cached by request hash, so re-asking the same question in the
  same generation costs nothing;
* context is bounded before it reaches the model.

The scheduler never runs eight models. It runs one, eight times.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from ecosystem.config import get_settings
from ecosystem.services.llm.base import LLMProvider, LLMRequest, LLMResponse

# Rough character budget per token; used to trim prompts without a tokeniser.
_CHARS_PER_TOKEN = 4


@dataclass(order=True)
class _QueuedRequest:
    priority: int
    sequence: int
    request: LLMRequest = field(compare=False)
    agent_id: str | None = field(default=None, compare=False)
    future: asyncio.Future = field(compare=False, default=None)


class InferenceScheduler:
    def __init__(self, provider: LLMProvider, max_concurrency: int | None = None):
        settings = get_settings()
        self.provider = provider
        self._semaphore = asyncio.Semaphore(max_concurrency or settings.llm_max_concurrency)
        self._queue: list[_QueuedRequest] = []
        self._counter = itertools.count()
        self._cache: dict[str, LLMResponse] = {}
        self._lock = asyncio.Lock()
        self._worker: asyncio.Task | None = None
        self._stats = {"served": 0, "cached": 0, "queued": 0, "total_ms": 0}

    # -- queue management ---------------------------------------------------

    async def submit(
        self,
        request: LLMRequest,
        *,
        priority: int = 5,
        agent_id: str | None = None,
    ) -> LLMResponse:
        """Enqueue a request and await its result. Lower priority runs first."""
        cached = self._cache.get(_key(request))
        if cached is not None:
            self._stats["cached"] += 1
            return cached.model_copy(update={"from_cache": True})

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        item = _QueuedRequest(priority, next(self._counter), request, agent_id, future)
        async with self._lock:
            heapq.heappush(self._queue, item)
            self._stats["queued"] += 1
        await self._ensure_worker()
        return await future

    async def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            async with self._lock:
                if not self._queue:
                    return
                item = heapq.heappop(self._queue)
            async with self._semaphore:
                await self._serve(item)

    async def _serve(self, item: _QueuedRequest) -> None:
        if item.future.cancelled():
            return
        started = time.perf_counter()
        try:
            response = await self.provider.generate(item.request)
            self._cache[_key(item.request)] = response
            self._stats["served"] += 1
            self._stats["total_ms"] += response.latency_ms or int(
                (time.perf_counter() - started) * 1000
            )
            if not item.future.done():
                item.future.set_result(response)
        except Exception as exc:
            if not item.future.done():
                item.future.set_exception(exc)

    # -- helpers ------------------------------------------------------------

    def bound_context(self, text: str, max_tokens: int | None = None) -> str:
        """Trim text to the configured context window, keeping the head and
        tail (instructions and the most recent evidence)."""
        settings = get_settings()
        budget = (max_tokens or settings.llm_max_context_tokens) * _CHARS_PER_TOKEN
        if len(text) <= budget:
            return text
        keep = budget // 2
        return f"{text[:keep]}\n...[truncated]...\n{text[-keep:]}"

    def clear_cache(self) -> None:
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "queue_depth": len(self._queue),
            "cache_entries": len(self._cache),
            "provider": self.provider.name,
        }


def _key(request: LLMRequest) -> str:
    payload = f"{request.system}|{request.prompt}|{request.temperature}|{request.max_tokens}"
    return hashlib.sha256(payload.encode()).hexdigest()


_scheduler: InferenceScheduler | None = None


def get_scheduler() -> InferenceScheduler:
    """The process-wide scheduler. One model, one queue, eight agents."""
    global _scheduler
    if _scheduler is None:
        _scheduler = InferenceScheduler(_build_provider())
    return _scheduler


def _build_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "ollama":
        from ecosystem.services.llm.ollama import OllamaProvider

        return OllamaProvider()
    from ecosystem.services.llm.mock import MockProvider

    return MockProvider()


def reset_scheduler() -> None:
    global _scheduler
    _scheduler = None
