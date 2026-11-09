"""Agentic evals for the gateway (Phase 6, DESIGN.md §11).

A *reference client* connects to the gateway over stdio exactly like Claude Desktop does, hands
its tools to an LLM, and runs each eval question as its own agent loop. Answers are graded
against *live oracles*: small functions that compute the right numbers straight from the
data layer, independently of the gateway's own services, so expectations stay correct as BLS
and FRED publish new data.

This package is test tooling. It is not part of the server (``src/econ_gateway``), which
contains no LLM (DESIGN.md decision 12).

Run: ``.venv/Scripts/python.exe -m evals --help``
"""
