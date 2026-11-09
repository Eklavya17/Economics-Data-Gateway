# Development

Working rules (coding, documentation, testing) are in [CLAUDE.md](../CLAUDE.md); design decisions
are in [DESIGN.md](../DESIGN.md).

## Checks

```bash
.venv/Scripts/ruff.exe format src tests evals scripts
```

```bash
.venv/Scripts/ruff.exe check src tests evals scripts
```

```bash
.venv/Scripts/pyright.exe
```

```bash
.venv/Scripts/python.exe -m pytest
```

Live checks hit the real BLS and FRED APIs. Run them with `LIVE_TESTS=1` set:

```bash
LIVE_TESTS=1 .venv/Scripts/python.exe -m pytest -m live
```

End-to-end over real stdio, the way Claude Desktop connects:

```bash
.venv/Scripts/python.exe scripts/smoke_live.py
```

End-to-end over real HTTP with JWT auth, the way the Docker image runs:

```bash
.venv/Scripts/python.exe scripts/smoke_http.py
```

## Generated docs

The README's tool, resource and prompt tables, [TOOLS.md](TOOLS.md), and the interface snapshots
in `tests/snapshots/` are generated from the server itself. After changing any tool, resource or
prompt (name, parameters, docstring, response model), regenerate them and review the diff. A test
fails until you do:

```bash
.venv/Scripts/python.exe scripts/gen_tool_docs.py
```

## Data maintenance

After each spring OEWS release, pre-load the new tables. The first load downloads and parses
large BLS files, which is too slow to do inside a tool call:

```bash
.venv/Scripts/python.exe scripts/warm_cache.py
```

## Interactive inspection

```bash
.venv/Scripts/fastmcp.exe dev inspector src/econ_gateway/server.py
```

## Evals

`evals/` holds a reference client and 22 realistic questions (14 dev, 8 held-out).
- **Client.** It connects to the gateway over stdio, as Claude Desktop does, and lets an LLM
  (OpenAI, or a local Ollama model) call the gateway's tools.
- **Live oracles.** Each expected answer is computed fresh with the plain data-source clients, so it
  never goes stale and never reuses the gateway's own code.
- **Grading.** Deterministic: numbers within a tolerance, the period, keywords, and "not
  available" where that is the honest answer.
- **Baseline.** Each question also runs without the gateway, to show what it adds.

Install the eval extra once:

```bash
uv pip install --python .venv/Scripts/python.exe -e ".[evals]"
```

Print the expected answers only (free, no LLM):

```bash
.venv/Scripts/python.exe -m evals --oracles-only --split all
```

Run the dev split with and without the gateway on OpenAI (uses `OPENAI_API_KEY`; billed):

```bash
.venv/Scripts/python.exe -m evals
```

Run with a local Ollama model instead (free; pull a tool-capable model yourself first):

```bash
.venv/Scripts/python.exe -m evals --provider ollama --model llama3.1
```

Other flags: `--split held_out|all`, `--mode gateway|baseline|both`, `--case <id>` (repeatable),
`--repeat N`. Reports go to `evals/reports/eval_<time>.md`. The matching `.json` holds every
conversation, so failures can be read rather than just counted. Tune against `dev` only; run
`held_out` to confirm.

Re-record the demo transcript in [DEMO.md](DEMO.md) (billed, about US$0.05):

```bash
.venv/Scripts/python.exe scripts/demo.py
```

## Layout

```
src/econ_gateway/
  server.py      FastMCP app: instructions, middleware, toolset mounting, CLI main()
  http_server.py Streamable HTTP: JWT auth, /health and /ready, Host checks, uvicorn runner
  config.py      ECON_* settings; loads .env
  consts.py      every limit, with units and source
  models/        Pydantic models = tool output schemas (series, places, compute) and
                 resource bodies (reference)
  toolsets/      MCP layer (thin): core, wages, regional, compute; shared annotations + errors
  services/      business logic, no MCP imports:
                   series:    search, describe, observations, transforms, metadata, gaps
                   places:    areas (one area_id across OEWS/QCEW/LAUS), wages (OEWS),
                              employment (QCEW)
                   compute:   real_wages, compare
                   reference: releases (schedules), reference (catalog, series, status),
                              methodology (notes), health (probes)
                   access:    upstream (+ upstream_places) over the sources clients;
                              reliability (guard limits), release_calendar (cache freshness)
  sources/       BLS, FRED, OEWS, QCEW clients, disk cache, catalog, get_series;
                 reference/ holds the curated catalog and OEWS/QCEW reference tables
  resolve/       BM25 catalog retrieval, occupation and area matching
  infra/         generic mechanisms: http (guarded adapter), rate_limit, breaker, quota,
                 single_flight, cache (release-aware DiskCache), telemetry, errors, logs
  resources.py   econ:// resources (catalog, series, releases, methodology, status)
  prompts.py     question templates (slash commands in Claude Code)
  methodology/   Markdown explainers served as econ://methodology/{topic}
  devtools/      tool_docs.py: generates README tables, docs/TOOLS.md, interface snapshots
evals/           reference client (OpenAI/Ollama), 22 questions with live oracles, grader, reports
tests/           unit tests on fakes, MCP contract tests, HTTP/auth tests, live tests
scripts/         smoke_live.py (stdio), smoke_http.py (HTTP + auth), demo.py (docs/DEMO.md),
                 warm_cache.py (pre-load OEWS), gen_tool_docs.py (generated docs)
Dockerfile, docker-compose.yml, .dockerignore   container build (docs/DEPLOYMENT.md)
server.json      MCP Registry metadata (not yet published; docs/PUBLISHING.md)
```
