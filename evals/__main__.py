"""Command line for the evals.

    .venv/Scripts/python.exe -m evals                          # dev split, gateway vs baseline
    .venv/Scripts/python.exe -m evals --split held_out --mode gateway
    .venv/Scripts/python.exe -m evals --case cpi_inflation --repeat 3
    .venv/Scripts/python.exe -m evals --oracles-only           # expected answers, no LLM calls
    .venv/Scripts/python.exe -m evals --provider ollama --model llama3.1   # local, free

Every LLM call is billed by the provider (OpenAI) unless it runs locally (Ollama).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

import econ_gateway  # noqa: F401  (loads .env, including the API keys)
from evals.agent import Mode
from evals.cases import CASES, select
from evals.llm import OpenAIChatModel
from evals.oracles import Oracle
from evals.report import summarize, write_report
from evals.runner import EvalAbortedError, compute_expectations, run_eval
from evals.settings import EvalSettings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals", description=(__doc__ or "").splitlines()[0]
    )
    parser.add_argument("--split", choices=["dev", "held_out", "all"], default="dev")
    parser.add_argument("--mode", choices=["gateway", "baseline", "both"], default="both")
    parser.add_argument("--case", action="append", default=[], help="run only this case ID")
    parser.add_argument("--repeat", type=int, default=1, help="attempts per case")
    parser.add_argument("--provider", choices=["openai", "ollama"], help="overrides EVAL_PROVIDER")
    parser.add_argument("--model", help="overrides EVAL_MODEL")
    parser.add_argument("--base-url", help="overrides EVAL_BASE_URL")
    parser.add_argument(
        "--oracles-only", action="store_true", help="print expected answers; no LLM calls"
    )
    return parser.parse_args()


def _settings(args: argparse.Namespace) -> EvalSettings:
    overrides = {
        key: value
        for key, value in (
            ("provider", args.provider),
            ("model", args.model),
            ("base_url", args.base_url),
        )
        if value
    }
    return EvalSettings(**overrides)


def main() -> int:
    """Run the cases and write a report. Exit 1: unknown case ID; exit 2: LLM unreachable."""
    args = _parse_args()
    cases = select(args.split, args.case)
    if not cases:
        known = ", ".join(c.case_id for c in CASES)
        print(f"No cases matched. Known case IDs: {known}", file=sys.stderr)
        return 1
    if args.oracles_only:
        for case_id, expected in compute_expectations(cases, Oracle(date.today())).items():
            print(f"{case_id}: {expected if isinstance(expected, str) else expected.truth}")
        return 0
    settings = _settings(args)
    model = OpenAIChatModel(settings)
    modes: list[Mode] = ["gateway", "baseline"] if args.mode == "both" else [args.mode]
    try:
        results = asyncio.run(run_eval(cases, model, settings, modes=modes, repeat=args.repeat))
    except EvalAbortedError as exc:
        print(f"Eval aborted: {exc}", file=sys.stderr)
        return 2
    meta = {
        "model": model.name,
        "split": args.split,
        "modes": ", ".join(modes),
        "repeat": args.repeat,
        "cases": len(cases),
        "date": date.today().isoformat(),
    }
    json_path, md_path = write_report(results, meta, settings.reports_dir)
    for row in summarize(results):
        print(row)
    print(f"report: {md_path}\ntranscripts: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
