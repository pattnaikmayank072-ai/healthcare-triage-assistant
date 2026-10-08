"""Environment-only configuration for the educational triage prototype.

No provider calls are made by importing this module. External model use remains
opt-in and requires explicit model IDs and provider keys at runtime.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    allow_external: bool = _truthy("ALLOW_EXTERNAL_LLM", False)
    mock_llm: bool = _truthy("MOCK_LLM", True)
    max_llm_calls_per_case: int = int(os.getenv("MAX_LLM_CALLS_PER_CASE", "8"))
    max_llm_input_chars: int = int(os.getenv("MAX_LLM_INPUT_CHARS", "12000"))
    max_llm_output_tokens: int = int(os.getenv("MAX_LLM_OUTPUT_TOKENS", "700"))
    gemini_model: str = os.getenv("GEMINI_MODEL", "")
    groq_model: str = os.getenv("GROQ_MODEL", "")
    gemini_key_configured: bool = bool(os.getenv("GEMINI_API_KEY"))
    groq_key_configured: bool = bool(os.getenv("GROQ_API_KEY"))
    sarvam_key_configured: bool = bool(os.getenv("SARVAM_API_KEY"))
    hf_token_configured: bool = bool(os.getenv("HF_TOKEN"))
    jwt_secret_configured: bool = bool(os.getenv("JWT_SIGNING_SECRET"))
    data_encryption_key_configured: bool = bool(os.getenv("DATA_ENCRYPTION_KEY"))
    synthetic_audio_allowed: bool = _truthy("SYNTHETIC_AUDIO_ALLOWED", False)
    api_rate_limit: str = os.getenv("API_RATE_LIMIT", "60/minute")
    login_rate_limit: str = os.getenv("LOGIN_RATE_LIMIT", "5/minute")
    max_request_bytes: int = int(os.getenv("MAX_REQUEST_BYTES", str(6 * 1024 * 1024)))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
    max_document_pages: int = int(os.getenv("MAX_DOCUMENT_PAGES", "5"))
    background_scheduler_enabled: bool = _truthy("BACKGROUND_SCHEDULER_ENABLED", True)
    watchdog_interval_seconds: int = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "60"))
    retention_days: int | None = (int(os.environ["RETENTION_DAYS"])
                                  if os.getenv("RETENTION_DAYS", "").strip() else None)
    allowed_origins: tuple[str, ...] = tuple(
        origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()
    )

    def provider_enabled(self, provider: str) -> bool:
        if not self.allow_external or self.mock_llm:
            return False
        if provider == "gemini":
            return self.gemini_key_configured and bool(self.gemini_model)
        if provider == "groq":
            return self.groq_key_configured and bool(self.groq_model)
        if provider == "sarvam":
            return self.sarvam_key_configured
        return False


settings = Settings()
