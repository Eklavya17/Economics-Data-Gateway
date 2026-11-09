"""Run eval cases through the reference client and grade them.

One gateway process (stdio, as Claude Desktop runs it) serves every question of a run; each
question still starts a fresh conversation. Oracles run once per case before any LLM call, so
a failing oracle (e.g. BLS down) is reported instead of being blamed on the model.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from evals.agent import AgentRun, Mode, ReferenceAgent
from evals.cases import Case
from evals.grader import Expectation, Grade, grade
from evals.llm import ChatModel
from evals.oracles import Oracle
from evals.settings import EvalSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# If this many answers in a row fail before any succeeds, the LLM endpoint is misconfigured
# (bad key, bad URL, no network): stop instead of paying for, or waiting on, the rest.
ABORT_AFTER_INITIAL_ERRORS = 3


class EvalAbortedError(RuntimeError):
    """The first answers all failed with infrastructure errors; the run was stopped."""


@dataclass
class CaseResult:
    """One graded answer.

    Attributes:
        case: The question.
        mode: ``gateway`` or ``baseline``.
        attempt: 1-based repeat number.
        expectation: What the oracle expected (``None`` if the oracle failed).
        grade: The grade (``None`` if the oracle failed).
        run: The agent loop record.
        oracle_error: Why the oracle failed, if it did.
    """

    case: Case
    mode: Mode
    attempt: int
    expectation: Expectation | None
    grade: Grade | None
    run: AgentRun
    oracle_error: str | None = None

    @property
    def passed(self) -> bool:
        """Graded and passed."""
        return self.grade is not None and self.grade.passed


def gateway_transport() -> StdioTransport:
    """Start the gateway the way an MCP host does: a stdio subprocess."""
    return StdioTransport(
        command=sys.executable, args=["-m", "econ_gateway.server"], cwd=str(PROJECT_ROOT)
    )


def compute_expectations(cases: list[Case], oracle: Oracle) -> dict[str, Expectation | str]:
    """Run every oracle; failures map to their error text instead of raising."""
    out: dict[str, Expectation | str] = {}
    for case in cases:
        try:
            out[case.case_id] = case.oracle(oracle)
        except Exception as exc:  # noqa: BLE001  (reported per case, never fatal)
            out[case.case_id] = f"{type(exc).__name__}: {exc}"
    return out


async def run_eval(
    cases: list[Case],
    model: ChatModel,
    settings: EvalSettings,
    *,
    modes: list[Mode],
    repeat: int = 1,
    today: date | None = None,
    gateway: Any = None,
    progress: bool = True,
) -> list[CaseResult]:
    """Run ``cases`` in each mode, ``repeat`` times, and grade every answer.

    Args:
        cases: Questions to ask.
        model: The LLM.
        settings: Loop limits.
        modes: ``gateway`` and/or ``baseline``.
        repeat: Attempts per case (to measure consistency).
        today: Reference date for oracles and the system prompt.
        gateway: What ``fastmcp.Client`` connects to; defaults to a stdio subprocess. Tests
            pass an in-memory server.
        progress: Print one line per answer to stderr.

    Returns:
        One result per case × mode × attempt.
    """
    today = today or date.today()
    expectations = compute_expectations(cases, Oracle(today))
    results: list[CaseResult] = []
    for mode in modes:
        if mode == "gateway":
            async with Client(gateway if gateway is not None else gateway_transport()) as client:
                agent = ReferenceAgent(model, settings, client)
                results += await _run_cases(
                    agent, cases, expectations, mode, repeat, today, progress
                )
        else:
            agent = ReferenceAgent(model, settings, None)
            results += await _run_cases(agent, cases, expectations, mode, repeat, today, progress)
    return results


async def _run_cases(
    agent: ReferenceAgent,
    cases: list[Case],
    expectations: dict[str, Expectation | str],
    mode: Mode,
    repeat: int,
    today: date,
    progress: bool,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        for attempt in range(1, repeat + 1):
            run = await agent.run(case.question, today=today)
            _abort_if_misconfigured(results, run)
            expected = expectations[case.case_id]
            if isinstance(expected, str):
                result = CaseResult(case, mode, attempt, None, None, run, oracle_error=expected)
            else:
                result = CaseResult(case, mode, attempt, expected, grade(run.answer, expected), run)
            results.append(result)
            if progress:
                status = (
                    "ORACLE-ERROR" if result.oracle_error else ("pass" if result.passed else "FAIL")
                )
                print(
                    f"[{mode}] {case.case_id} #{attempt}: {status} "
                    f"({run.tool_calls} calls, {run.input_tokens + run.output_tokens} tokens, "
                    f"{run.latency_s}s)",
                    file=sys.stderr,
                )
    return results


def _abort_if_misconfigured(previous: list[CaseResult], run: AgentRun) -> None:
    """Raise when ``run`` and every answer before it in this mode failed with an error."""
    if run.stopped != "error" or any(r.run.stopped != "error" for r in previous):
        return
    if len(previous) + 1 >= ABORT_AFTER_INITIAL_ERRORS:
        raise EvalAbortedError(
            f"The first {len(previous) + 1} answers all failed; stopping. Last error: {run.error}"
        )
