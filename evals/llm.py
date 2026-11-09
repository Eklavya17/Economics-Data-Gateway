"""The LLM side of the reference client: one Chat Completions call, normalized.

``ChatModel`` is a Protocol so tests can script replies without a network. ``OpenAIChatModel``
uses the ``openai`` SDK (``chat.completions.create``), which also works against Ollama's
OpenAI-compatible endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import econ_gateway  # noqa: F401  (importing the package loads .env, incl. OPENAI_API_KEY)
from evals.settings import EvalSettings

Message = dict[str, Any]  # a Chat Completions message (role, content, tool_calls, ...)


@dataclass
class ToolCall:
    """One function call requested by the model.

    Attributes:
        call_id: ID to echo in the tool result message.
        name: Tool name.
        arguments: Raw JSON arguments as the model wrote them (may be invalid JSON).
    """

    call_id: str
    name: str
    arguments: str


@dataclass
class Completion:
    """A normalized model reply.

    Attributes:
        text: Assistant text (may be empty when only tools are called).
        tool_calls: Requested tool calls, in order.
        input_tokens: Prompt tokens billed for this call.
        output_tokens: Completion tokens billed for this call.
        message: The assistant message to append to the conversation as-is.
    """

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    message: Message = field(default_factory=dict)


class ChatModel(Protocol):
    """Anything that can answer a conversation, optionally calling tools."""

    @property
    def name(self) -> str:
        """Provider and model, for reports."""
        ...

    async def complete(
        self, messages: list[Message], tools: list[dict[str, Any]], *, allow_tools: bool = True
    ) -> Completion:
        """Run one model call over ``messages``.

        Args:
            messages: The conversation so far.
            tools: Function definitions (Chat Completions format); may be empty.
            allow_tools: False forces a text answer (``tool_choice="none"``).
        """
        ...


class OpenAIChatModel:
    """``ChatModel`` over the OpenAI Chat Completions API (OpenAI or Ollama)."""

    def __init__(self, settings: EvalSettings):
        from openai import AsyncOpenAI  # optional dependency: pip install -e ".[evals]"

        key = os.getenv("OPENAI_API_KEY", "").strip()
        if settings.provider == "openai" and not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Put it in the gateway's .env, or use "
                "--provider ollama with a local model."
            )
        self._client = AsyncOpenAI(
            api_key=key or "ollama",  # Ollama ignores the key, but the SDK requires one
            base_url=settings.resolved_base_url,
        )
        self._model = settings.resolved_model
        self._provider = settings.provider
        self._temperature = settings.temperature

    @property
    def name(self) -> str:
        """``provider/model``."""
        return f"{self._provider}/{self._model}"

    async def complete(
        self, messages: list[Message], tools: list[dict[str, Any]], *, allow_tools: bool = True
    ) -> Completion:
        """One ``chat.completions.create`` call, normalized to a ``Completion``."""
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto" if allow_tools else "none"
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self._temperature,
            **kwargs,
        )
        choice = response.choices[0].message
        calls = [
            ToolCall(call.id, call.function.name, call.function.arguments or "{}")
            for call in (choice.tool_calls or [])
            if getattr(call, "function", None) is not None  # skip non-function (custom) calls
        ]
        message: Message = {"role": "assistant", "content": choice.content or ""}
        if calls:
            message["tool_calls"] = [
                {
                    "id": c.call_id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in calls
            ]
        usage = response.usage
        return Completion(
            text=choice.content or "",
            tool_calls=calls,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            message=message,
        )
