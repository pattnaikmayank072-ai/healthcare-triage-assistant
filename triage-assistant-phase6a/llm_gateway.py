"""Single optional LLM gateway. No provider SDK or network client is created here.

Providers are usable only when an explicitly injected adapter and the settings gate both
allow them. External text is redacted and rescanned before an adapter can receive it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agents.safety import SafetyAgent
from config import settings

ProviderAdapter = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class GatewayResult:
    status: str
    output: Any = None
    provider: str | None = None
    model: str | None = None
    attempts: int = 0
    payload_fingerprint: str = ""
    flags: tuple[str, ...] = ()


class LLMGateway:
    """Central model boundary with explicit provider allowlists and safe fallback."""

    EXTERNAL = {"gemini", "groq", "sarvam"}

    def __init__(self, adapters: Mapping[str, ProviderAdapter] | None = None,
                 safety_agent: SafetyAgent | None = None, *,
                 sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[float, float], float] = random.uniform,
                 clock: Callable[[], float] = time.monotonic):
        self.adapters = dict(adapters or {})
        self.safety = safety_agent or SafetyAgent()
        self.sleep = sleep
        self.jitter = jitter
        self.clock = clock
        self._cache: dict[str, tuple[Any, str, str | None]] = {}
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._case_calls: dict[str, int] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _model(provider: str) -> str | None:
        if provider == "gemini":
            return settings.gemini_model or None
        if provider == "groq":
            return settings.groq_model or None
        if provider == "local":
            return __import__("os").getenv("LOCAL_MODEL_ID") or None
        if provider == "sarvam":
            return "sarvam-configured"
        return None

    def _provider_enabled(self, provider: str) -> bool:
        if provider == "local":
            return provider in self.adapters
        return provider in self.EXTERNAL and settings.provider_enabled(provider)

    def _safe_payload(self, payload: Any, *, external: bool) -> dict[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        clean = copy.deepcopy(dict(payload))
        serialized = self._canonical(clean)
        if len(serialized) > settings.max_llm_input_chars:
            return None
        if external:
            try:
                clean = self._redact_recursive(clean)
            except Exception:
                return None
        return clean

    def _redact_recursive(self, value: Any) -> Any:
        if isinstance(value, str):
            if not value:
                return value
            return self.safety.prepare_external_text(value)
        if isinstance(value, dict):
            return {key: self._redact_recursive(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_recursive(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact_recursive(item) for item in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise ValueError("unsupported gateway payload value")

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
        return status_code == 429 or (isinstance(status_code, int) and 500 <= status_code <= 599)

    def _attempt_provider(self, provider: str, task: str, payload: dict[str, Any],
                          fingerprint: str) -> GatewayResult | None:
        adapter = self.adapters.get(provider)
        if adapter is None or not self._provider_enabled(provider):
            return None
        now = self.clock()
        with self._lock:
            if self._open_until.get(provider, 0.0) > now:
                return None
        model = self._model(provider)
        attempts = 0
        for attempt in range(3):
            attempts += 1
            try:
                response = adapter({
                    "task": task,
                    "model": model,
                    "payload": copy.deepcopy(payload),
                    "max_output_tokens": settings.max_llm_output_tokens,
                    "prompt_fingerprint": fingerprint,
                })
                with self._lock:
                    self._failures[provider] = 0
                    self._open_until.pop(provider, None)
                    self._cache[fingerprint] = (copy.deepcopy(response), provider, model)
                return GatewayResult("completed", response, provider, model, attempts, fingerprint, ())
            except Exception as exc:
                with self._lock:
                    failures = self._failures.get(provider, 0) + 1
                    self._failures[provider] = failures
                    if failures >= 3:
                        self._open_until[provider] = self.clock() + 60.0
                if not self._retryable(exc) or attempt >= 2 or failures >= 3:
                    break
                base_delay = 0.25 * (2 ** attempt)
                self.sleep(base_delay + self.jitter(0.0, base_delay * 0.2))
        return None

    def call(self, *, task: str, payload: Mapping[str, Any], primary_provider: str,
             allowed_providers: tuple[str, ...] = ("gemini", "groq", "local"),
             case_id: str | None = None) -> GatewayResult:
        """Run the primary→Groq→local chain, then signal rules/template fallback.

        ``None`` output means the caller must use its deterministic fallback. Safety,
        schema, and validation failures are not retried by this gateway.
        """
        if not isinstance(task, str) or not task.strip() or len(task) > 120:
            return GatewayResult("fallback", flags=("invalid_gateway_task",))
        if primary_provider not in {"gemini", "groq", "local", "sarvam"}:
            return GatewayResult("fallback", flags=("provider_not_allowlisted",))
        if case_id:
            with self._lock:
                calls = self._case_calls.get(case_id, 0)
                if calls >= settings.max_llm_calls_per_case:
                    return GatewayResult("fallback", flags=("per_case_llm_call_cap_reached",))
                self._case_calls[case_id] = calls + 1
        candidates = [primary_provider]
        if primary_provider != "groq" and "groq" in allowed_providers:
            candidates.append("groq")
        if "local" in allowed_providers and primary_provider != "local":
            candidates.append("local")
        candidates = list(dict.fromkeys(p for p in candidates if p in allowed_providers or p == primary_provider))
        attempts = 0
        flags: list[str] = []
        for provider in candidates:
            external = provider in self.EXTERNAL
            safe_payload = self._safe_payload(payload, external=external)
            if safe_payload is None:
                flags.append("payload_egress_blocked_or_over_limit")
                if external:
                    continue
                return GatewayResult("fallback", payload_fingerprint="", flags=tuple(dict.fromkeys(flags)))
            fingerprint_input = self._canonical({"task": task, "payload": safe_payload})
            fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
            with self._lock:
                cached = self._cache.get(fingerprint)
            if cached is not None:
                value, cached_provider, cached_model = cached
                return GatewayResult("completed", copy.deepcopy(value), cached_provider,
                                     cached_model, 0, fingerprint, ("prompt_fingerprint_cache_hit",))
            result = self._attempt_provider(provider, task, safe_payload, fingerprint)
            if result is not None:
                return result
            flags.append(f"{provider}_unavailable_or_circuit_open")
        flags.append("rules_templates_fallback_required")
        return GatewayResult("fallback", attempts=attempts,
                             payload_fingerprint="", flags=tuple(dict.fromkeys(flags)))

    def clear_cache(self) -> None:
        """Clear transient outputs between test runs or operator-managed sessions."""
        with self._lock:
            self._cache.clear()
            self._case_calls.clear()
