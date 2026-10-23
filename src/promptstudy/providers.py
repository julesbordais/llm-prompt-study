"""Provider adapters (OpenAI, Anthropic, Mistral, Mock) behind one interface,
wrapped by LLMClient = retry + cache + token/cost/latency accounting."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from .cache import ResponseCache, request_key
from .costs import call_cost_usd
from .retry import with_retries


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    provider: str
    model: str
    cost_usd: Optional[float] = None
    cached: bool = False
    retries: int = 0


class BaseProvider:
    name = "base"

    def __init__(self, model: str, timeout: float = 60.0):
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, messages: list[dict], temperature: Optional[float],
                 max_tokens: int, json_schema: Optional[dict]) -> tuple[str, int, int]:
        raise NotImplementedError


def _require(env: str) -> str:
    val = os.getenv(env)
    if not val:
        raise RuntimeError(f"{env} is not set. Put it in .env (never hard-code keys).")
    return val


class OpenAIProvider(BaseProvider):
    """Chat Completions + JSON mode. Validation is done by Pydantic afterwards."""
    name = "openai"

    def __init__(self, model: str, timeout: float = 60.0):
        super().__init__(model, timeout)
        from openai import OpenAI  # lazy import: only needed if used
        self.client = OpenAI(api_key=_require("OPENAI_API_KEY"), timeout=timeout, max_retries=0)

    def complete(self, system, messages, temperature, max_tokens, json_schema):
        kwargs = dict(model=self.model, messages=[{"role": "system", "content": system}, *messages],
                      max_completion_tokens=max_tokens, response_format={"type": "json_object"})
        if temperature is not None:
            kwargs["temperature"] = temperature
        r = self.client.chat.completions.create(**kwargs)
        return r.choices[0].message.content or "", r.usage.prompt_tokens, r.usage.completion_tokens


class AnthropicProvider(BaseProvider):
    """Messages API + forced tool calling against the Pydantic JSON Schema."""
    name = "anthropic"

    def __init__(self, model: str, timeout: float = 60.0):
        super().__init__(model, timeout)
        from anthropic import Anthropic
        self.client = Anthropic(api_key=_require("ANTHROPIC_API_KEY"), timeout=timeout, max_retries=0)

    def complete(self, system, messages, temperature, max_tokens, json_schema):
        kwargs = dict(model=self.model, system=system, messages=messages, max_tokens=max_tokens)
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_schema is not None:
            kwargs["tools"] = [{"name": "submit", "description": "Submit the structured answer.",
                                "input_schema": json_schema}]
            kwargs["tool_choice"] = {"type": "tool", "name": "submit"}
        r = self.client.messages.create(**kwargs)
        text = ""
        for block in r.content:
            if getattr(block, "type", "") == "tool_use":
                text = json.dumps(block.input, ensure_ascii=False)
                break
            if getattr(block, "type", "") == "text":
                text += block.text
        return text, r.usage.input_tokens, r.usage.output_tokens


class MistralProvider(BaseProvider):
    name = "mistral"

    def __init__(self, model: str, timeout: float = 60.0):
        super().__init__(model, timeout)
        from mistralai import Mistral
        self.client = Mistral(api_key=_require("MISTRAL_API_KEY"), timeout_ms=int(timeout * 1000))

    def complete(self, system, messages, temperature, max_tokens, json_schema):
        kwargs = dict(model=self.model, messages=[{"role": "system", "content": system}, *messages],
                      max_tokens=max_tokens, response_format={"type": "json_object"})
        if temperature is not None:
            kwargs["temperature"] = temperature
        r = self.client.chat.complete(**kwargs)
        return r.choices[0].message.content or "", r.usage.prompt_tokens, r.usage.completion_tokens


class OpenRouterProvider(BaseProvider):
    """OpenRouter: one OpenAI-compatible endpoint for OpenAI / Anthropic / Mistral / Llama models.
    Model = OpenRouter slug, e.g. 'openai/gpt-4o-mini'. Uses plain HTTP (requests) so no extra SDK is needed.
    The real USD cost of each call is returned by OpenRouter (usage accounting) and used directly."""
    name = "openrouter"
    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str, timeout: float = 60.0):
        super().__init__(model, timeout)
        import requests
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {_require('OPENROUTER_API_KEY')}",
                                     "Content-Type": "application/json", "X-Title": "LLM Prompt Study"})
        self.last_cost: Optional[float] = None

    @property
    def family(self) -> str:  # 'openai/gpt-4o-mini' -> 'openai' (self-enhancement check)
        return self.model.split("/", 1)[0]

    def complete(self, system, messages, temperature, max_tokens, json_schema):
        body = dict(model=self.model, messages=[{"role": "system", "content": system}, *messages],
                    max_tokens=max_tokens, response_format={"type": "json_object"}, usage={"include": True})
        if temperature is not None:
            body["temperature"] = temperature
        r = self.session.post(self.URL, json=body, timeout=self.timeout)
        if r.status_code >= 400:
            err = RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
            err.status_code = r.status_code  # type: ignore[attr-defined]  -> classified by retry.py
            err.response = r  # type: ignore[attr-defined]  -> Retry-After honoured
            raise err
        data = r.json()
        if "error" in data:  # OpenRouter can return 200 with an upstream error
            err = RuntimeError(f"OpenRouter upstream error: {data['error']}")
            err.status_code = int(data["error"].get("code") or 502) if str(data["error"].get("code", "")).isdigit() else 502  # type: ignore[attr-defined]
            raise err
        u = data.get("usage") or {}
        self.last_cost = u.get("cost")
        text = (data["choices"][0]["message"].get("content") or "")
        return text, int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))


def make_provider(spec: str, timeout: float = 60.0) -> BaseProvider:
    """spec = 'provider:model', e.g. 'openai:gpt-4o-mini' or 'mock:mock-small'."""
    provider, _, model = spec.partition(":")
    provider = provider.strip().lower()
    if provider == "mock":
        from .mock import MockProvider
        return MockProvider(model or "mock-small")
    cls = {"openrouter": OpenRouterProvider, "openai": OpenAIProvider, "anthropic": AnthropicProvider, "mistral": MistralProvider}.get(provider)
    if cls is None or not model:
        raise ValueError(f"Bad model spec '{spec}'. Use provider:model with provider in openrouter|openai|anthropic|mistral|mock")
    return cls(model.strip(), timeout)


@dataclass
class UsageLedger:
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    retries: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, r: LLMResponse) -> None:
        with self._lock:
            self.calls += 1
            self.cached_calls += int(r.cached)
            if not r.cached:
                self.input_tokens += r.input_tokens
                self.output_tokens += r.output_tokens
                self.cost_usd += r.cost_usd or 0.0
            self.retries += r.retries


class LLMClient:
    def __init__(self, spec: str, cache: Optional[ResponseCache] = None, max_attempts: int = 5,
                 timeout: float = 60.0, ledger: Optional[UsageLedger] = None):
        self.spec = spec
        self.provider = make_provider(spec, timeout)
        self.cache = cache
        self.max_attempts = max_attempts
        self.ledger = ledger or UsageLedger()

    @property
    def family(self) -> str:
        return getattr(self.provider, "family", self.provider.name)

    def call(self, system: str, messages: list[dict], temperature: Optional[float] = 0.0,
             max_tokens: int = 800, json_schema: Optional[dict] = None, tag: str = "") -> LLMResponse:
        payload = dict(spec=self.spec, system=system, messages=messages, temperature=temperature,
                       max_tokens=max_tokens, schema=json_schema, tag=tag)
        key = request_key(payload)
        if self.cache and (hit := self.cache.get(key)):
            r = LLMResponse(**{**hit, "cached": True})
            self.ledger.add(r)
            return r

        retries = {"n": 0}

        @with_retries(max_attempts=self.max_attempts,
                      on_retry=lambda *_: retries.__setitem__("n", retries["n"] + 1))
        def _do():
            return self.provider.complete(system, messages, temperature, max_tokens, json_schema)

        t0 = time.perf_counter()
        text, tin, tout = _do()
        latency = time.perf_counter() - t0
        r = LLMResponse(text=text, input_tokens=tin, output_tokens=tout, latency_s=latency,
                        provider=self.provider.name, model=self.provider.model,
                        cost_usd=getattr(self.provider, "last_cost", None) or call_cost_usd(self.provider.model, tin, tout),
                        retries=retries["n"])
        if self.cache:
            self.cache.put(key, {k: v for k, v in asdict(r).items() if k != "cached"})
        self.ledger.add(r)
        return r
