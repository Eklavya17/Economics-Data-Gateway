"""Record a demo: real questions answered through the gateway, written to docs/DEMO.md.

Uses the eval reference client (``evals/``): it starts the gateway over stdio like Claude
Desktop does, gives an LLM its tools, and records every tool call, an excerpt of each result,
and the final answer. The transcript is a recording of one real run against live BLS/FRED data,
not a mock-up; re-run it to refresh the numbers.

    .venv/Scripts/python.exe scripts/demo.py                    # OpenAI (billed, ~US$0.05)
    .venv/Scripts/python.exe scripts/demo.py --provider ollama --model llama3.1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the evals package lives at the project root

from fastmcp import Client  # noqa: E402

import econ_gateway  # noqa: E402,F401  (loads .env files)
from evals.agent import AgentRun, ReferenceAgent  # noqa: E402
from evals.llm import OpenAIChatModel  # noqa: E402
from evals.runner import gateway_transport  # noqa: E402
from evals.settings import EvalSettings  # noqa: E402

DEMO_PATH = ROOT / "docs" / "DEMO.md"
RESULT_EXCERPT_CHARS = 700  # per tool result in the transcript

QUESTIONS = [
    # The demo from "Project Ideas.pdf".
    "What did wages look like for registered nurses in Texas last year?",
    "Have manufacturing workers' hourly earnings kept up with inflation over the past year?",
    "How is the job market in Travis County, Texas? Give jobs, pay and unemployment.",
    "How has the U.S. unemployment rate changed over the past year?",
]


def _excerpt(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= RESULT_EXCERPT_CHARS:
        return compact
    return compact[:RESULT_EXCERPT_CHARS] + f" ... ({len(compact):,} characters in full)"


def _transcript(question: str, run: AgentRun) -> list[str]:
    """Markdown for one question: tool calls with result excerpts, then the answer."""
    results = {
        m.get("tool_call_id"): str(m.get("content", ""))
        for m in run.messages
        if m["role"] == "tool"
    }
    calls = [
        call for m in run.messages if m["role"] == "assistant" for call in m.get("tool_calls", [])
    ]
    lines = [f"## {question}", ""]
    for number, call in enumerate(calls, start=1):
        name, args = call["function"]["name"], call["function"]["arguments"]
        lines += [f"**{number}. `{name}`** `{args}`", ""]
        lines += ["```json", _excerpt(results.get(call["id"], "")), "```", ""]
    lines += ["**Answer**", ""]
    lines += [f"> {line}" if line else ">" for line in run.answer.splitlines()]
    tokens = run.input_tokens + run.output_tokens
    lines += [
        "",
        f"_{run.tool_calls} tool calls, {run.tool_errors} errors, {tokens:,} tokens, "
        f"{run.latency_s} s._",
        "",
    ]
    return lines


async def record(settings: EvalSettings) -> str:
    """Run every question and return the Markdown document."""
    model = OpenAIChatModel(settings)
    today = date.today()
    parts = [
        "# Demo: questions answered through the gateway",
        "",
        f"Recorded {today:%B %d, %Y} by `scripts/demo.py`. The model is `{model.name}`, driven "
        "by the eval reference client: it connects to the gateway over stdio, as Claude Desktop "
        "does, and may only use the gateway's tools. Every number below came from live BLS and "
        "FRED data at recording time. Tool results are shortened; the model saw them in full.",
        "",
        "Re-record: `.venv/Scripts/python.exe scripts/demo.py`",
        "",
    ]
    async with Client(gateway_transport()) as client:
        agent = ReferenceAgent(model, settings, client)
        for question in QUESTIONS:
            run = await agent.run(question, today=today)
            if run.stopped == "error":
                raise SystemExit(f"Demo question failed: {question}: {run.error}")
            print(f"recorded: {question} ({run.tool_calls} calls)", file=sys.stderr)
            parts += _transcript(question, run)
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    """Record the demo and write docs/DEMO.md."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--provider", choices=["openai", "ollama"])
    parser.add_argument("--model")
    args = parser.parse_args()
    overrides = {k: v for k, v in (("provider", args.provider), ("model", args.model)) if v}
    document = asyncio.run(record(EvalSettings(**overrides)))
    DEMO_PATH.write_text(document, encoding="utf-8")
    print(f"wrote {DEMO_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
