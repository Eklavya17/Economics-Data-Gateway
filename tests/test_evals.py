"""Phase 6 eval harness, offline: grader, case set, agent loop, runner and report.

The agent loop runs against the real MCP server in memory (with the fake upstream) and a
scripted model, so no LLM or network is involved.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from conftest import TODAY, FakeUpstream
from econ_gateway.config import Settings
from econ_gateway.server import create_server
from evals.agent import BUDGET_EXHAUSTED, ReferenceAgent
from evals.cases import CASES, Case, select
from evals.grader import Expect, Expectation, extract_numbers, grade
from evals.llm import Completion, Message, ToolCall
from evals.oracles import day_variants, month_variants, quarter_variants
from evals.report import summarize, write_report
from evals.runner import EvalAbortedError, run_eval
from evals.settings import EvalSettings

# ---------------------------------------------------------------- grader


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The median was $95,970 in May 2025.", 95970),
        ("Unemployment was 4.3% in September.", 4.3),
        ("Payrolls fell by -32,000 jobs.", -32000),
        ("about 159.3 million jobs", 159_300_000),
        ("931K covered jobs", 931_000),
        ("7.5 million openings", 7_500_000),
    ],
)
def test_extract_numbers_understands_formats(text: str, expected: float):
    assert any(abs(v - expected) < 1e-6 for v in extract_numbers(text))


def test_expect_tolerances_and_sign_free():
    assert Expect("rate", [4.3], abs_tol=0.05).matches([4.33])
    assert not Expect("rate", [4.3], abs_tol=0.05).matches([4.4])
    assert Expect("level", [931218], rel_tol=0.005).matches([931000])
    assert not Expect("change", [-0.2], abs_tol=0.05).matches([0.2])
    assert Expect("change", [-0.2], abs_tol=0.05, sign_free=True).matches([0.2])


def test_period_match_does_not_confuse_days():
    expectation = Expectation(periods=[day_variants(date(2026, 11, 1))])
    assert not grade("Claims for the week ending November 12.", expectation).passed
    assert grade("Claims for the week ending Nov 1, 2026.", expectation).passed


def test_grade_combines_numbers_periods_keywords():
    expectation = Expectation(
        numbers=[Expect("median", [95970], rel_tol=0.002)],
        periods=[["May 2025"]],
        keywords=[["registered nurse"]],
    )
    good = grade("Registered nurses in Texas earned a median $95,970 in May 2025.", expectation)
    partial = grade("Registered nurses earned about $90,000.", expectation)
    assert good.passed and good.score == 1.0
    assert not partial.passed and partial.score == pytest.approx(1 / 3)


def test_abstention_requires_saying_so():
    expectation = Expectation(abstain=True)
    assert grade(
        "That data has not been released yet; December 2027 is in the future.", expectation
    ).passed
    assert not grade("It was 4.1%.", expectation).passed


def test_period_variants_cover_common_spellings():
    assert {"September 2026", "Sept 2026", "2026-09"} <= set(month_variants(date(2026, 9, 1)))
    assert {"Q1 2026", "first quarter of 2026"} <= set(quarter_variants(2026, 1))
    assert "Nov 10" in day_variants(date(2026, 11, 10))


# ---------------------------------------------------------------- case set


def test_case_set_is_well_formed():
    ids = [c.case_id for c in CASES]
    assert len(ids) == len(set(ids))
    assert len(select("dev")) == 14 and len(select("held_out")) == 8
    assert len(select("all")) == len(CASES)
    assert select("all", ["cpi_inflation"])[0].split == "dev"
    for case in CASES:
        assert "?" in case.question and len(case.question) > 20


async def test_expected_tools_exist():
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as client:
        tools = {t.name for t in await client.list_tools()}
    assert {name for case in CASES for name in case.expected_tools} <= tools


# ---------------------------------------------------------------- agent loop


class ScriptedModel:
    """Replays canned completions and records what it was given."""

    def __init__(self, script: list[Completion]):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "scripted/test"

    async def complete(
        self, messages: list[Message], tools: list[dict[str, Any]], *, allow_tools: bool = True
    ) -> Completion:
        self.calls.append({"tools": len(tools), "allow_tools": allow_tools, "n": len(messages)})
        return self.script.pop(0) if self.script else _answer("done")


def _tool(name: str, arguments: dict[str, Any] | str, call_id: str = "c1") -> Completion:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    call = ToolCall(call_id, name, raw)
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}
        ],
    }
    return Completion("", [call], input_tokens=100, output_tokens=10, message=message)


def _answer(text: str) -> Completion:
    return Completion(text, [], 50, 20, {"role": "assistant", "content": text})


@pytest.fixture
def gateway():
    return create_server(Settings(), upstream=FakeUpstream())


async def test_agent_calls_gateway_tools_and_answers(gateway):
    model = ScriptedModel(
        [_tool("get_latest", {"series_ids": ["LNS14000000"]}), _answer("It is 4.33% (Sep 2026).")]
    )
    async with Client(gateway) as client:
        run = await ReferenceAgent(model, EvalSettings(), client).run("Unemployment?", today=TODAY)
    assert run.stopped == "answered" and run.answer.startswith("It is 4.33%")
    assert (run.tool_calls, run.tool_errors, run.turns) == (1, 0, 2)
    assert (run.input_tokens, run.output_tokens) == (150, 30)
    tool_message = next(m for m in run.messages if m["role"] == "tool")
    assert json.loads(tool_message["content"])["results"][0]["meta"]["series_id"] == "LNS14000000"
    assert "econ-gateway" in run.messages[0]["content"]  # server instructions in the prompt
    assert model.calls[0]["tools"] == 10


async def test_agent_records_tool_errors(gateway):
    model = ScriptedModel(
        [
            _tool("get_latest", "{not json", "a"),
            _tool("no_such_tool", {}, "b"),
            _tool("describe_series", {"series_id": "LNU99999999"}, "c"),
            _answer("Could not find it."),
        ]
    )
    async with Client(gateway) as client:
        run = await ReferenceAgent(model, EvalSettings(), client).run("?", today=TODAY)
    assert run.tool_errors == 3
    assert "Invalid JSON" in (run.steps[0].error or "")
    assert "Unknown tool" in (run.steps[1].error or "")
    assert "search_series" in (run.steps[2].error or "")  # the gateway's actionable message


async def test_agent_forces_an_answer_when_budget_is_spent(gateway):
    calls = [_tool("get_latest", {"series_ids": ["LNS14000000"]}, f"c{i}") for i in range(5)]
    model = ScriptedModel([*calls, _answer("final")])
    settings = EvalSettings(max_tool_calls=2, max_turns=10)
    async with Client(gateway) as client:
        run = await ReferenceAgent(model, settings, client).run("?", today=TODAY)
    assert run.tool_calls == 2
    assert run.stopped == "max_tool_calls" and run.answer == "final"
    assert model.calls[-1]["allow_tools"] is False
    assert any(m.get("content") == BUDGET_EXHAUSTED for m in run.messages)


async def test_baseline_mode_offers_no_tools():
    model = ScriptedModel([_answer("I don't know the current rate.")])
    run = await ReferenceAgent(model, EvalSettings(), None).run("Unemployment?", today=TODAY)
    assert run.mode == "baseline" and model.calls[0]["tools"] == 0
    assert "no tools" in run.messages[0]["content"]


async def test_agent_survives_model_errors(gateway):
    class Broken(ScriptedModel):
        async def complete(self, messages, tools, *, allow_tools=True):  # type: ignore[override]
            raise RuntimeError("rate limited")

    async with Client(gateway) as client:
        run = await ReferenceAgent(Broken([]), EvalSettings(), client).run("?", today=TODAY)
    assert run.stopped == "error" and "rate limited" in (run.error or "")


# ---------------------------------------------------------------- runner and report


def _fixed_case(case_id: str, split: str, value: float) -> Case:
    return Case(
        case_id, split, "test", "What is the unemployment rate now?",  # type: ignore[arg-type]
        lambda o: Expectation(numbers=[Expect("rate", [value], abs_tol=0.05)], truth=f"{value}%"),
    )  # fmt: skip


async def test_runner_grades_both_modes_and_writes_report(gateway, tmp_path: Path):
    cases = [_fixed_case("right", "dev", 4.33), _fixed_case("wrong", "held_out", 9.9)]
    script = []
    for _ in cases:  # gateway mode: one tool call, then an answer quoting the tool's value
        script += [_tool("get_latest", {"series_ids": ["LNS14000000"]}), _answer("It is 4.33%.")]
    script += [_answer("I don't know."), _answer("I don't know.")]  # baseline mode
    model = ScriptedModel(script)
    results = await run_eval(
        cases, model, EvalSettings(), modes=["gateway", "baseline"], today=TODAY,
        gateway=gateway, progress=False,
    )  # fmt: skip
    assert [(r.mode, r.case.case_id, r.passed) for r in results] == [
        ("gateway", "right", True),
        ("gateway", "wrong", False),
        ("baseline", "right", False),
        ("baseline", "wrong", False),
    ]
    rows = {(row["mode"], row["split"]): row for row in summarize(results)}
    assert rows[("gateway", "dev")]["accuracy"] == 1.0
    assert rows[("gateway", "dev")]["tool_calls_per_answer"] == 1.0
    assert rows[("baseline", "dev")]["accuracy"] == 0.0

    json_path, md_path = write_report(results, {"model": model.name}, tmp_path)
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(saved["results"]) == 4 and saved["results"][0]["messages"]
    markdown = md_path.read_text(encoding="utf-8")
    assert "## Failures" in markdown and "`wrong`" in markdown and "`get_latest`: 2" in markdown


async def test_oracle_failure_is_reported_not_raised(gateway):
    def broken(o):
        raise KeyError("BLS down")

    case = Case("broken", "dev", "test", "What is the rate now?", broken)
    results = await run_eval(
        [case], ScriptedModel([_answer("4%")]), EvalSettings(), modes=["baseline"],
        today=TODAY, progress=False,
    )  # fmt: skip
    assert results[0].oracle_error and "BLS down" in results[0].oracle_error
    assert summarize(results)[0]["oracle_errors"] == 1


async def test_runner_stops_early_when_the_llm_is_unreachable():
    class Unreachable(ScriptedModel):
        async def complete(self, messages, tools, *, allow_tools=True):  # type: ignore[override]
            raise ConnectionError("no route to host")

    cases = [_fixed_case(f"c{i}", "dev", 4.0) for i in range(5)]
    with pytest.raises(EvalAbortedError, match="no route to host"):
        await run_eval(
            cases, Unreachable([]), EvalSettings(), modes=["baseline"], today=TODAY,
            progress=False,
        )  # fmt: skip


def test_empty_openai_base_url_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    assert EvalSettings().resolved_base_url == "https://api.openai.com/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    assert EvalSettings().resolved_base_url == "https://proxy.example/v1"
    assert EvalSettings(provider="ollama").resolved_base_url == "http://localhost:11434/v1"
