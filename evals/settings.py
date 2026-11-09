"""Eval settings, from environment variables prefixed ``EVAL_`` (CLI flags override them).

The LLM is reached through the OpenAI-compatible Chat Completions API, which covers both
OpenAI and a local Ollama server (``http://localhost:11434/v1``). The OpenAI key is the shared
``OPENAI_API_KEY`` from ``.env``; Ollama needs no key.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

EVALS_DIR = Path(__file__).resolve().parent
REPORTS_DIR = EVALS_DIR / "reports"

Provider = Literal["openai", "ollama"]
DEFAULT_MODELS: dict[Provider, str] = {
    "openai": "gpt-4.1-mini",  # Project 1's eval model; cheap and good at tool use
    "ollama": "llama3.1",  # any local model with tool support; pull it yourself
}
DEFAULT_BASE_URLS: dict[Provider, str] = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
}


class EvalSettings(BaseSettings):
    """How the reference client talks to the LLM and how long an agent loop may run.

    Attributes:
        provider: ``openai`` or ``ollama``.
        model: Model name; defaults per provider (``DEFAULT_MODELS``).
        base_url: API base URL; defaults per provider.
        temperature: Sampling temperature; 0 makes runs as repeatable as the API allows.
        max_turns: LLM calls per question before the client forces a final answer.
        max_tool_calls: Tool calls per question before the client forces a final answer.
        max_result_chars: Longest tool result passed back to the model (the gateway already
            caps responses; this is a safety net for small-context local models).
        reports_dir: Where reports and transcripts are written.
    """

    model_config = SettingsConfigDict(env_prefix="EVAL_", extra="ignore")

    provider: Provider = "openai"
    model: str | None = None
    base_url: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_turns: int = Field(default=10, ge=1, le=50)
    max_tool_calls: int = Field(default=16, ge=1, le=100)
    max_result_chars: int = Field(default=30_000, ge=1_000)
    reports_dir: Path = REPORTS_DIR

    @property
    def resolved_model(self) -> str:
        """The model to call."""
        return self.model or DEFAULT_MODELS[self.provider]

    @property
    def resolved_base_url(self) -> str:
        """The API base URL to call.

        For OpenAI, a non-empty ``OPENAI_BASE_URL`` is honored (proxies, compatible servers).
        Always passing a URL matters: an ``.env`` may define ``OPENAI_BASE_URL=`` (empty),
        and the SDK would otherwise read that and send requests to a URL with no scheme.
        """
        if self.base_url:
            return self.base_url
        if self.provider == "openai":
            return os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_BASE_URLS["openai"]
        return DEFAULT_BASE_URLS[self.provider]
