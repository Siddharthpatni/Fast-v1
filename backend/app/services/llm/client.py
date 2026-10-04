"""Provider-agnostic LLM client for structured (JSON-schema) extraction.

Providers, all via plain HTTP (no SDK):
- ollama:     local models; decoding constrained by the JSON schema
- openai:     OpenAI API; strict json_schema structured outputs
- openrouter: OpenAI-compatible gateway, used here for its free models. A
              comma-separated LLM_MODEL becomes OpenRouter's `models` fallback list.

LLM_PROVIDER may also be a comma-separated chain (e.g. "openrouter,ollama"):
each provider is tried in order, so a rate-limited free model falls back to the
local one.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import settings

PROVIDERS = ("ollama", "openai", "openrouter")
DEFAULT_MODELS = {
    "ollama": "qwen2.5:7b-instruct",
    "openai": "gpt-4o-mini",
    # Order from data/eval/benchmark_dev.json (10 free models, cut short by the daily
    # quota at 4-9 of 30 reports each): best fully-correct rate, then speed. Re-rank
    # with `python -m app.services.llm.evaluate --models ...` when a full run fits the quota
    "openrouter": (
        "nvidia/nemotron-3-super-120b-a12b:free,cohere/north-mini-code:free,"
        "inclusionai/ling-3.0-flash-sante:free"
    ),
}
RATE_LIMIT_RETRIES = 2

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """The LLM call failed or returned something that is not the requested JSON."""


class QuotaExhaustedError(LLMError):
    """The provider's daily request quota is used up until `reset_at` (epoch seconds)."""

    def __init__(self, message: str, reset_at: float) -> None:
        super().__init__(message)
        self.reset_at = reset_at


def _parse_json(content: str) -> dict[str, Any]:
    """Parse model output, tolerating ```json fences and reasoning text around the object."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    try:
        return json.loads(content)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(content[start : end + 1])  # type: ignore[no-any-return]


class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        daily_limit: int | None = None,
    ) -> None:
        self.provider = (provider or settings.llm_provider).strip().lower()
        if self.provider not in PROVIDERS:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")
        self.models = [
            m.strip() for m in (model or DEFAULT_MODELS[self.provider]).split(",") if m.strip()
        ]
        self.model = self.models[0]
        default_url = {
            "ollama": settings.ollama_base_url,
            "openai": settings.openai_base_url,
            "openrouter": settings.openrouter_base_url,
        }[self.provider]
        self.base_url = (base_url or default_url).rstrip("/")
        headers: dict[str, str] = {}
        if self.provider == "openai":
            headers["Authorization"] = (
                f"Bearer {api_key if api_key is not None else settings.openai_api_key}"
            )
        elif self.provider == "openrouter":
            key = api_key if api_key is not None else settings.openrouter_api_key
            headers = {"Authorization": f"Bearer {key}", "X-Title": "FastMile"}
        self._client = httpx.AsyncClient(
            headers=headers, timeout=timeout or settings.llm_timeout, transport=transport
        )
        self.last_model: str | None = None  # Which model answered (OpenRouter may fall back)
        self.ready = False  # Startup probe passed: key accepted, models exist
        self.quota_reset_at = 0.0  # While in the future, don't call: the daily quota is used up
        self.last_error: str | None = None  # Shown in /health until a call succeeds again
        self.calls_ok = 0
        self.daily_limit = daily_limit  # Our own cap on calls per UTC day (for paid models)
        self._day = ""
        self.calls_today = 0

    async def extract_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._extract_json(system, user, schema)
        except LLMError as exc:
            self.last_error = str(exc)[:200]
            raise
        self.last_error = None
        self.calls_ok += 1
        return result

    async def _extract_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if time.time() < self.quota_reset_at:
            raise QuotaExhaustedError(f"{self.provider} daily quota used up", self.quota_reset_at)
        if self.daily_limit is not None:
            today = time.strftime("%Y-%m-%d", time.gmtime())
            if today != self._day:
                self._day, self.calls_today = today, 0
            if self.calls_today >= self.daily_limit:
                midnight = (int(time.time()) // 86400 + 1) * 86400
                raise QuotaExhaustedError(
                    f"{self.model}: daily cap of {self.daily_limit} calls reached", midnight
                )
            self.calls_today += 1
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            if self.provider == "ollama":
                response = await self._client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "format": schema,
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                content = response.json()["message"]["content"]
            else:
                content = await self._chat_completions(messages, schema)
            return _parse_json(content)
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"{self.provider} returned {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.provider} request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"{self.provider} returned an unexpected response: {exc}") from exc

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            response = await self._client.post(f"{self.base_url}/chat/completions", json=body)
            if response.status_code != 429:
                return response
            if "per-day" in response.text or "daily" in response.text.lower():
                # Retrying can't help before the reset; remember it and stop calling
                reset_ms = response.headers.get("x-ratelimit-reset", "")
                self.quota_reset_at = (
                    int(reset_ms) / 1000 if reset_ms.isdigit() else time.time() + 3600
                )
                raise QuotaExhaustedError(
                    f"{self.provider} daily quota used up", self.quota_reset_at
                )
            if attempt == RATE_LIMIT_RETRIES:
                return response
            await asyncio.sleep(2 * (attempt + 1))  # Per-minute limits clear quickly
        return response

    async def _chat_completions(
        self, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> str:
        base: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if self.provider == "openrouter" and len(self.models) > 1:
            base["models"] = self.models  # OpenRouter tries these in order
        body = {
            **base,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": schema, "strict": True},
            },
        }
        if self.provider == "openrouter":
            # Only route to providers that honour structured outputs
            body["provider"] = {"require_parameters": True}
        response = await self._post(body)
        if self.provider == "openrouter" and response.status_code in (400, 404):
            # No endpoint of these models supports structured outputs right now: put the
            # schema in the prompt and parse leniently. Decided per call, since free
            # endpoints come and go and the next report should try structured output again
            system = messages[0]["content"] + (
                "\n\nReply with only one JSON object, no prose, matching this JSON schema:\n"
                + json.dumps(schema)
            )
            response = await self._post(
                {**base, "messages": [{"role": "system", "content": system}, *messages[1:]]}
            )
        response.raise_for_status()
        data = response.json()
        if "error" in data:  # OpenRouter can return 200 with an error body
            raise LLMError(f"{self.provider} error: {str(data['error'])[:300]}")
        self.last_model = data.get("model", self.model)
        return data["choices"][0]["message"]["content"]  # type: ignore[no-any-return]

    async def probe(self) -> None:
        """Check at startup that the key is accepted and the models exist, without using quota.

        OpenRouter: GET /key and /models (neither counts as a request). Ollama: the model is
        pulled. OpenAI: GET /models. Missing models are dropped; if none is left, or the key
        is rejected, the error shows in /health instead of "not used yet".
        """
        try:
            if self.provider == "ollama":
                response = await self._client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                available = {m["name"] for m in response.json().get("models", [])}
            else:
                if self.provider == "openrouter":
                    key = await self._client.get(f"{self.base_url}/key")
                    if key.status_code in (401, 403):
                        raise LLMError(f"{self.provider} rejected the API key")
                    key.raise_for_status()
                response = await self._client.get(f"{self.base_url}/models")
                response.raise_for_status()
                available = {m["id"] for m in response.json().get("data", [])}
        except httpx.HTTPError as exc:
            self.last_error = f"{self.provider} unreachable at startup: {exc}"[:200]
            return
        except LLMError as exc:
            self.last_error = str(exc)
            return
        missing = [m for m in self.models if m not in available]
        if missing:
            logger.warning("%s: models not available, skipped: %s", self.provider, missing)
            self.models = [m for m in self.models if m in available]
        if not self.models:
            self.last_error = f"{self.provider}: none of the configured models exist: {missing}"
            return
        self.model, self.ready, self.last_error = self.models[0], True, None

    async def close(self) -> None:
        await self._client.aclose()

    def _status(self) -> str:
        # "error" while the last call (or the probe) failed; "ok" once a report was read;
        # "ready" when the probe passed but nothing was read yet; else "untested"
        if self.last_error:
            return "error"
        if self.calls_ok:
            return "ok"
        return "ready" if self.ready else "untested"

    def describe(self) -> dict[str, str]:
        info = {"provider": self.provider, "model": ",".join(self.models), "status": self._status()}
        if self.last_error:
            info["last_error"] = self.last_error
        if time.time() < self.quota_reset_at:
            reset = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(self.quota_reset_at))
            info["quota_exhausted_until"] = reset
        if self.daily_limit is not None:
            info["calls_today"] = f"{self.calls_today} of {self.daily_limit}"
        return info


class FallbackLLMClient:
    """Tries several providers in order; the first that returns valid JSON wins."""

    def __init__(self, clients: list[LLMClient]) -> None:
        self.clients = clients
        self.answered_by: LLMClient | None = None  # Who answered the last report
        self.last_error: str | None = None

    async def extract_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        errors = []
        for client in self.clients:
            try:
                result = await client.extract_json(system, user, schema)
            except LLMError as exc:
                errors.append(str(exc))
                continue
            self.answered_by, self.last_error = client, None
            return result
        self.answered_by, self.last_error = None, " | ".join(errors)[:300]
        raise LLMError(" | ".join(errors))

    async def probe(self) -> None:
        await asyncio.gather(*(client.probe() for client in self.clients))
        if not any(c.ready for c in self.clients):
            self.last_error = " | ".join(c.last_error or "" for c in self.clients)[:300]

    async def close(self) -> None:
        for client in self.clients:
            await client.close()

    def describe(self) -> dict[str, Any]:
        # The chain works if the last report was read, whichever link answered it
        if self.last_error:
            status = "error"
        elif self.answered_by is not None:
            status = "ok"
        elif any(c.ready for c in self.clients):
            status = "ready"
        else:
            status = "untested"
        info: dict[str, Any] = {
            "provider": ",".join(dict.fromkeys(c.provider for c in self.clients)),
            "model": ",".join(m for c in self.clients for m in c.models),
            "status": status,
            "chain": [c.describe() for c in self.clients],
        }
        if self.answered_by is not None:
            info["answered_by"] = self.answered_by.model
        if self.last_error:
            info["last_error"] = self.last_error
        return info


def make_llm_client() -> LLMClient | FallbackLLMClient | None:
    """The configured client(s), or None when LLM_PROVIDER=none (rule-based extraction only).

    LLM_MODEL applies to the first provider in the chain; later ones use their defaults.
    """
    providers = [p.strip().lower() for p in settings.llm_provider.split(",") if p.strip()]
    providers = [p for p in providers if p != "none"]
    if not providers:
        return None
    clients = [
        LLMClient(p, model=(settings.llm_model or None) if i == 0 else None)
        for i, p in enumerate(providers)
    ]
    if settings.llm_paid_model and "openrouter" in providers:
        # Right after the free OpenRouter models, before any slower provider
        paid = LLMClient(
            "openrouter", model=settings.llm_paid_model, daily_limit=settings.llm_paid_daily_limit
        )
        clients.insert(providers.index("openrouter") + 1, paid)
    return clients[0] if len(clients) == 1 else FallbackLLMClient(clients)
