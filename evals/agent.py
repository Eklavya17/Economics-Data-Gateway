"""The reference client: an LLM agent loop over the gateway's MCP tools.

It behaves like a typical MCP host (Claude Desktop, Claude Code): list the server's tools, put
the server's ``instructions`` in the system prompt, let the model call tools until it answers,
and pass every tool result (including errors) back to the model. It records what happened so
the eval can measure accuracy *and* cost: tool calls, tool errors, tokens, turns and latency.

Two modes:

- ``gateway``: the model gets the gateway's tools.
- ``baseline``: no tools, answering from the model's own knowledge. Comparing the two shows
  what the gateway adds (DESIGN.md §11).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from fastmcp import Client

from evals.llm import ChatModel, Message, ToolCall
from evals.settings import EvalSettings

Mode = Literal["gateway", "baseline"]

GATEWAY_SYSTEM = """\
You answer questions about U.S. economic data using the tools of the econ-gateway MCP server.
Today's date is {today}. Answer with the specific numbers, the period each refers to, and the \
source. If the data needed is not published or not available, say so plainly instead of \
estimating.

Server instructions:
{instructions}"""

BASELINE_SYSTEM = """\
You answer questions about U.S. economic data. You have no tools and no internet access.
Today's date is {today}. If you do not know the current figures, say so instead of guessing."""

# Sent as the tool result when the per-question budget is spent, so every tool call the model
# made still gets an answer (the API rejects a conversation with unanswered tool calls).
BUDGET_EXHAUSTED = "Not run: the tool-call budget for this question is used up. Answer now."


@dataclass
class ToolStep:
    """One tool call made by the model.

    Attributes:
        name: Tool name.
        arguments: Parsed arguments, or the raw string when the model wrote invalid JSON.
        is_error: Whether the call failed (invalid JSON, unknown tool, or ``isError`` result).
        latency_ms: Time for the MCP call.
        result_chars: Size of the result passed back to the model.
        error: Error text when ``is_error``.
    """

    name: str
    arguments: dict[str, Any] | str
    is_error: bool
    latency_ms: int
    result_chars: int
    error: str | None = None


@dataclass
class AgentRun:
    """Everything one question's agent loop produced.

    Attributes:
        question: The question asked.
        mode: ``gateway`` or ``baseline``.
        answer: The model's final text.
        steps: Tool calls in order.
        turns: LLM calls made.
        input_tokens: Prompt tokens across all LLM calls.
        output_tokens: Completion tokens across all LLM calls.
        latency_s: Wall-clock time for the whole loop.
        stopped: ``answered``, ``max_turns`` or ``max_tool_calls`` (the last two mean the
            client forced a final answer), or ``error``.
        error: Exception text when ``stopped == "error"``.
        messages: The full conversation, for reading transcripts.
    """

    question: str
    mode: Mode
    answer: str = ""
    steps: list[ToolStep] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    stopped: str = "answered"
    error: str | None = None
    messages: list[Message] = field(default_factory=list)

    @property
    def tool_calls(self) -> int:
        """Number of tool calls."""
        return len(self.steps)

    @property
    def tool_errors(self) -> int:
        """Number of failed tool calls."""
        return sum(step.is_error for step in self.steps)


def mcp_tool_to_function(tool: Any) -> dict[str, Any]:
    """Convert an MCP tool (name, description, inputSchema) to a Chat Completions function."""
    parameters = dict(tool.input_schema or {"type": "object", "properties": {}})
    parameters.setdefault("type", "object")
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": parameters,
        },
    }


class ReferenceAgent:
    """Runs questions through ``model``, with the gateway's tools when ``client`` is given.

    Args:
        model: The LLM.
        settings: Loop limits.
        client: A connected FastMCP client for the gateway, or ``None`` for baseline mode.
    """

    def __init__(self, model: ChatModel, settings: EvalSettings, client: Client | None = None):
        self._model = model
        self._settings = settings
        self._client = client
        self._tools: list[dict[str, Any]] | None = None
        self._tool_names: set[str] = set()

    @property
    def mode(self) -> Mode:
        """``gateway`` when tools are available, else ``baseline``."""
        return "gateway" if self._client is not None else "baseline"

    async def _load_tools(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        if self._tools is None:
            listed = await self._client.list_tools()
            self._tools = [mcp_tool_to_function(t) for t in listed]
            self._tool_names = {t.name for t in listed}
        return self._tools

    def _system_prompt(self, today: date) -> str:
        if self._client is None:
            return BASELINE_SYSTEM.format(today=today.isoformat())
        init = self._client.initialize_result
        instructions = (init.instructions if init else None) or "(none)"
        return GATEWAY_SYSTEM.format(today=today.isoformat(), instructions=instructions)

    async def run(self, question: str, *, today: date) -> AgentRun:
        """Answer one question in a fresh conversation.

        Never raises: an LLM or transport failure is recorded as ``stopped == "error"`` so one
        bad question does not end an eval run.
        """
        run = AgentRun(question=question, mode=self.mode)
        run.messages = [
            {"role": "system", "content": self._system_prompt(today)},
            {"role": "user", "content": question},
        ]
        started = time.perf_counter()
        try:
            await self._loop(run)
        except Exception as exc:  # recorded and graded as a failure, never fatal
            run.stopped, run.error = "error", f"{type(exc).__name__}: {exc}"
        run.latency_s = round(time.perf_counter() - started, 2)
        return run

    async def _loop(self, run: AgentRun) -> None:
        tools = await self._load_tools()
        while True:
            if run.turns >= self._settings.max_turns + 2:  # model ignored tool_choice="none"
                run.stopped = "max_turns"
                return
            out_of_turns = run.turns >= self._settings.max_turns - 1
            out_of_calls = run.tool_calls >= self._settings.max_tool_calls
            completion = await self._model.complete(
                run.messages, tools, allow_tools=not (out_of_turns or out_of_calls)
            )
            run.turns += 1
            run.input_tokens += completion.input_tokens
            run.output_tokens += completion.output_tokens
            run.messages.append(completion.message)
            if not completion.tool_calls:
                run.answer = completion.text.strip()
                if out_of_turns or out_of_calls:
                    run.stopped = "max_turns" if out_of_turns else "max_tool_calls"
                return
            for call in completion.tool_calls:
                content = await self._execute(call, run)
                run.messages.append(
                    {"role": "tool", "tool_call_id": call.call_id, "content": content}
                )

    async def _execute(self, call: ToolCall, run: AgentRun) -> str:
        """Run one tool call and record it; returns the text passed back to the model."""
        if run.tool_calls >= self._settings.max_tool_calls:
            return BUDGET_EXHAUSTED
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return self._record(run, call.name, call.arguments, 0, f"Invalid JSON arguments: {exc}")
        if call.name not in self._tool_names or self._client is None:
            return self._record(run, call.name, arguments, 0, f"Unknown tool '{call.name}'.")
        started = time.perf_counter()
        result = await self._client.call_tool(call.name, arguments, raise_on_error=False)
        latency_ms = round((time.perf_counter() - started) * 1000)
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content)
            return self._record(run, call.name, arguments, latency_ms, text or "Tool error.")
        if result.structured_content is not None:
            text = json.dumps(result.structured_content, separators=(",", ":"))
        else:
            text = " ".join(getattr(block, "text", "") for block in result.content)
        limit = self._settings.max_result_chars
        if len(text) > limit:
            text = text[:limit] + " ...[truncated by the eval client]"
        run.steps.append(ToolStep(call.name, arguments, False, latency_ms, len(text)))
        return text

    @staticmethod
    def _record(
        run: AgentRun, name: str, arguments: dict[str, Any] | str, latency_ms: int, error: str
    ) -> str:
        run.steps.append(ToolStep(name, arguments, True, latency_ms, len(error), error))
        return error
