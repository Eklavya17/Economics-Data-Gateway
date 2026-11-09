"""Metrics and reports for an eval run (JSON with full transcripts, plus a Markdown summary).

Metrics follow the Anthropic eval guidance (RESEARCH.md §3): accuracy, tool calls, tool errors,
tokens and latency, per mode (gateway vs. baseline) and split (dev vs. held-out). The JSON keeps
every conversation so failures can be read, not just counted.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from evals.runner import CaseResult


def _mean(values: list[float]) -> float:
    return round(mean(values), 2) if values else 0.0


def summarize(results: list[CaseResult]) -> list[dict[str, Any]]:
    """One row of metrics per (mode, split), in a stable order.

    ``accuracy`` counts only graded answers; answers whose oracle failed are listed separately.
    """
    rows: list[dict[str, Any]] = []
    groups = sorted(
        {(r.mode, r.case.split) for r in results}, key=lambda g: (g[0] != "gateway", g[1])
    )
    for mode, split in groups:
        group = [r for r in results if r.mode == mode and r.case.split == split]
        graded = [r for r in group if r.grade is not None]
        rows.append(
            {
                "mode": mode,
                "split": split,
                "answers": len(group),
                "accuracy": round(sum(r.passed for r in graded) / len(graded), 3)
                if graded
                else 0.0,
                "mean_score": _mean([r.grade.score for r in graded if r.grade]),
                "tool_calls_per_answer": _mean([r.run.tool_calls for r in group]),
                "tool_errors": sum(r.run.tool_errors for r in group),
                "input_tokens_per_answer": _mean([r.run.input_tokens for r in group]),
                "output_tokens_per_answer": _mean([r.run.output_tokens for r in group]),
                "latency_s_per_answer": _mean([r.run.latency_s for r in group]),
                "forced_stops": sum(r.run.stopped != "answered" for r in group),
                "oracle_errors": sum(r.oracle_error is not None for r in group),
            }
        )
    return rows


def _result_record(result: CaseResult) -> dict[str, Any]:
    run = result.run
    return {
        "case_id": result.case.case_id,
        "split": result.case.split,
        "category": result.case.category,
        "mode": result.mode,
        "attempt": result.attempt,
        "question": result.case.question,
        "passed": result.passed,
        "score": result.grade.score if result.grade else None,
        "checks": [asdict(c) for c in result.grade.checks] if result.grade else [],
        "truth": result.expectation.truth if result.expectation else None,
        "oracle_error": result.oracle_error,
        "answer": run.answer,
        "expected_tools": list(result.case.expected_tools),
        "tools_used": [s.name for s in run.steps],
        "steps": [asdict(s) for s in run.steps],
        "turns": run.turns,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "latency_s": run.latency_s,
        "stopped": run.stopped,
        "error": run.error,
        "messages": run.messages,
    }


def render_markdown(results: list[CaseResult], meta: dict[str, Any]) -> str:
    """Human-readable report: summary, per-case table, failures, tool use and tool errors."""
    lines = [f"# Eval run {meta['run_id']}", ""]
    lines += [f"- **{k}:** {v}" for k, v in meta.items() if k != "run_id"]
    lines += ["", "## Summary", ""]
    summary = summarize(results)
    if summary:
        keys = list(summary[0])
        lines += ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
        lines += ["| " + " | ".join(str(row[k]) for k in keys) + " |" for row in summary]
    lines += ["", "## Cases", ""]
    lines += [
        "| mode | split | case | result | score | calls | errors | tokens | s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        status = "oracle error" if r.oracle_error else ("pass" if r.passed else "**fail**")
        score = f"{r.grade.score:.2f}" if r.grade else "-"
        tokens = r.run.input_tokens + r.run.output_tokens
        lines.append(
            f"| {r.mode} | {r.case.split} | `{r.case.case_id}` | {status} | {score} | "
            f"{r.run.tool_calls} | {r.run.tool_errors} | {tokens} | {r.run.latency_s} |"
        )
    lines += _failures_section(results) + _tools_section(results)
    return "\n".join(lines) + "\n"


def _failures_section(results: list[CaseResult]) -> list[str]:
    failed = [r for r in results if not r.passed]
    if not failed:
        return ["", "## Failures", "", "None."]
    lines = ["", "## Failures", ""]
    for r in failed:
        lines += [f"### `{r.case.case_id}` ({r.mode}, attempt {r.attempt})", ""]
        lines += [f"- **Question:** {r.case.question}"]
        if r.oracle_error:
            lines += [f"- **Oracle error:** {r.oracle_error}", ""]
            continue
        lines += [f"- **Expected:** {r.expectation.truth if r.expectation else ''}"]
        missed = [c for c in (r.grade.checks if r.grade else []) if not c.passed]
        lines += ["- **Missed:** " + "; ".join(f"{c.name} ({c.detail})" for c in missed)]
        if r.run.error:
            lines += [f"- **Run error:** {r.run.error}"]
        answer = " ".join(r.run.answer.split())
        lines += [f"- **Answer:** {answer[:600]}{'...' if len(answer) > 600 else ''}", ""]
    return lines


def _tools_section(results: list[CaseResult]) -> list[str]:
    used = Counter(s.name for r in results for s in r.run.steps)
    lines = ["", "## Tool use (gateway mode)", ""]
    lines += [f"- `{name}`: {count}" for name, count in used.most_common()] or ["None."]
    errors = [(r.case.case_id, s) for r in results for s in r.run.steps if s.is_error]
    lines += ["", "## Tool errors", ""]
    if not errors:
        return lines + ["None."]
    for case_id, step in errors:
        args = json.dumps(step.arguments)[:200]
        lines.append(f"- `{case_id}` -> `{step.name}({args})`: {(step.error or '')[:300]}")
    return lines


def write_report(
    results: list[CaseResult], meta: dict[str, Any], directory: Path
) -> tuple[Path, Path]:
    """Write ``eval_<run_id>.json`` (with transcripts) and ``eval_<run_id>.md``.

    Returns:
        ``(json_path, markdown_path)``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": datetime.now().strftime("%Y%m%d-%H%M%S"), **meta}
    stem = directory / f"eval_{meta['run_id']}"
    payload = {
        "meta": meta,
        "summary": summarize(results),
        "results": [_result_record(r) for r in results],
    }
    json_path, md_path = stem.with_suffix(".json"), stem.with_suffix(".md")
    json_path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(results, meta), encoding="utf-8")
    return json_path, md_path
