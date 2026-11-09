# Economics Data Gateway — working rules

An MCP server (Python, FastMCP 4) that gives any AI assistant live, cited U.S. economic data
from BLS and FRED (CES, CPS/LAUS, CPI, JOLTS, OEWS, QCEW, jobless claims, GDP, rates).
Project 2 of `ECO_Projects`. It runs on its own; Project 1 (Labor Market Intelligence Agent) is a
separate project with a different job (README "How this differs from Project 1").

**Read first:** `docs/RESEARCH.md` (sources and the reasons behind these rules) and `DESIGN.md`
(architecture and phases, once written). Update `DESIGN.md` when a decision changes.

---

## Non-negotiables

1. **Numbers come only from official data.** Tools never estimate, interpolate or "fill in".
   If data is missing, say so in the response.
2. **The model never needs to invent an ID.** There is always a discovery tool that returns
   valid IDs, and every "not found" error points to it.
3. **Every response carries provenance**: source, series_id, URL, units, seasonal adjustment,
   reference period, `retrieved_at`, preliminary/stale flags.
4. **Read-only.** Every tool sets `readOnlyHint=True` and `openWorldHint=True`.
5. **Secrets stay on the server.** API keys come from environment variables only. They are
   never tool parameters and never appear in responses or logs.
6. **stdout belongs to the protocol.** All logging goes to stderr. A stray `print()` breaks stdio mode.
7. **Upstream hosts are a fixed allow-list, HTTPS only.** No tool fetches a URL supplied by the
   client or model.

---

## Keep it distinct from Project 1

The gateway is infrastructure: no LLM, no generated code, no prose. The only LLM in this
repository is the eval client in `evals/`, which is test tooling: `src/econ_gateway` must never
import `evals` or an LLM SDK. Never add a tool that
wraps Project 1's agent, analysis recipes, charts or briefing writer (DESIGN.md decision 12).
Shared code is limited to data plumbing.

## The data layer is ours

The gateway owns its data layer (DESIGN.md decision 54). `sources/` holds the BLS, FRED, OEWS and
QCEW clients, the disk cache, the catalog and `get_series`; `resolve/` holds the catalog retriever
and the occupation and area matchers. Both were copied from Project 1 at commit `bdfb586` and are
maintained here. Change them here, with tests in `tests/test_sources.py`; nothing is imported from
Project 1 at run time.
- The clients are synchronous, so write tools as plain `def`. FastMCP 4 runs sync tools in a
  worker thread (`run_in_thread=True` by default).
- Use `async def` only when a tool must `await` the `Context` (progress, elicitation).
- Always build the clients with the gateway's guarded session and release-aware cache
  (`BLSClient(session=self.reliability.session(), cache=self._cache)`). A bare `BLSClient()`
  bypasses the allow-list, rate limits, quota and breaker (DESIGN.md decision 23).

---

## Code layout

```
src/econ_gateway/
  __init__.py        # __version__ + module docstring
  server.py          # FastMCP app, instructions, middleware, mounts toolsets, main()
  http_server.py     # Streamable HTTP: JWT auth, /health, /ready, Host checks, uvicorn
  consts.py          # UPPER_CASE constants, each with a units comment
  config.py          # pydantic-settings Settings; validates env at startup
  models/            # Pydantic response models: series.py, places.py (re-exported by __init__)
  toolsets/          # one sub-server per toolset: core.py wages.py regional.py
  services/          # business logic; no MCP imports; unit-testable
  infra/             # generic mechanisms: guarded HTTP, rate limit, breaker, quota, single-flight,
                     # release-aware cache, logs (no economics; services/ sets their limits)
  resources.py       # econ:// resources (catalog, series, releases, methodology, status)
  prompts.py         # question templates; registered only when their toolsets are mounted
  methodology/       # Markdown notes served as econ://methodology/{topic} (package data)
tests/  evals/  docs/
```

- **Layers:** `toolsets` → `services` → `sources` / `resolve`. Tool functions stay thin: validate,
  call a service, shape the response. Business logic never lives in a tool function.
- `services/` must not import `fastmcp`, so it can be tested and reused without MCP.
- `infra/` must not import `services/` or `toolsets/`: it holds mechanisms, and `services/`
  chooses their limits from `consts.py`.
- One responsibility per module. Split a file once it passes ~400 lines.

---

## Tool rules (from the Anthropic guide, the MCP spec and awslabs)

- **≤ 12 tools in total.** Before adding one, ask whether an existing tool could take a parameter instead.
- **Names:** snake_case `verb_noun` (`search_series`, `get_occupation_wages`). Toolsets add a
  namespace prefix through `mount(namespace=...)`.
- **Parameters:**
  - Use `Annotated[T, Field(description=..., examples=[...])]`.
  - Use `Literal[...]` for fixed choices, `ge`/`le` bounds on numbers, and clear names
    (`series_id`, `start_date`, never `id`, `s`).
- **Tool docstring template** (shown to the model, so write it for the model):
  ```python
  """One sentence on what this answers.

  ## Usage
  When to call this vs. other tools; which tool to call first (e.g. "get IDs from search_series").

  ## Tips
  Domain gotchas: SA vs NSA, OEWS = May reference period, rates change in percentage points.

  ## Output format
  Fields returned, units, how truncation is signalled.

  IMPORTANT: <directive to the model> because <reason>.
  """
  ```
- **Returns:** a Pydantic model (which produces `outputSchema` and `structuredContent`) plus a
  short text summary.
  - Default to `response_format="concise"`.
  - Cap output with `max_points` and a total-size check.
  - When cutting, set `truncated=True` and include a `hint` on how to narrow the query.
- **Dates:** always return `latest_available`, and don't assume the model knows today's date.
  Never put the current year into queries.
- **Errors:** raise `ToolError` with three parts: what was wrong, what to do, a valid example.
  - e.g. `"Unknown series 'XYZ'. Call search_series('<topic>') to get valid IDs, e.g. 'LNS14000000'."`
  - Separate client mistakes from upstream failures.
  - With `mask_error_details=True`, unexpected exceptions never leak internals.
- **Batch tools** return per-item results plus a `failed` list. One bad ID never fails the whole call.

---

## Documentation and readability standards

- **Every module** starts with a docstring: what it does, its upstream docs URL (for clients),
  and anything non-obvious.
- **Every public function and class** gets a Google-style docstring (`Args:`, `Returns:`,
  `Raises:`). Pydantic models get an `Attributes:` section. Private helpers get a one-liner
  if the name isn't self-explanatory.
- **Comments explain *why*, not *what*.** Write them for economic-data quirks, e.g.
  `# BLS returns "-" for unavailable periods` or `# metro CPI is NSA-only`. Don't comment
  obvious code.
- **Full type hints everywhere.** `from __future__ import annotations`. pyright in standard
  mode must pass.
- **Naming:** descriptive names with units (`timeout_s`, `ttl_hours`, `max_points`). No
  one-letter names outside comprehensions.
- **Magic numbers** go in `consts.py` with a units comment and a source, e.g.
  `BLS_MAX_SERIES_PER_REQUEST = 50  # with API key; bls.gov/developers`.
- **Small functions** (aim ≤ 40 lines) with early returns. No nesting beyond 3 levels.
- **The README tool table is generated** by `scripts/gen_tool_docs.py` from the live server, and
  a test fails if the README drifts. All environment variables are documented in `.env.example`
  and the README.
- **Record every design decision** in `DESIGN.md` under the relevant section, with the reason.
  Append eval findings and the fixes they led to, as Project 1 did.

---

## Style and tooling

- Python ≥ 3.11. `ruff format` (line length 100) + `ruff check`
  (`select = ["E","F","I","B","UP","SIM","N","D"]`, Google docstring convention) + `pyright`.
- Pin exact versions for `fastmcp` and `mcp`. Record any version bump in `DESIGN.md`.
- Logging: stdlib `logging` to stderr with structured JSON. `ECON_LOG_LEVEL` defaults to WARNING.
  Log each external call with source, endpoint, latency and cache hit.
- Config: `pydantic-settings`, with environment variables prefixed `ECON_`; `BLS_API_KEY` and
  `FRED_API_KEY` live in this project's `.env`.

---

## Testing

- `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`).
- **Unit:** services with recorded HTTP fixtures. No network.
- **MCP:** in-memory `Client(transport=mcp)` calls every tool, including error paths.
- **Contract:** snapshot every tool's name, description and input/output schema. Changes must
  be deliberate.
- **Live:** marked `live`, run only with `LIVE_TESTS=1`.
- **Coverage:** ≥ 80% on `services/` and `infra/`.
- **Evals** (`evals/`): realistic questions with live oracles. Track accuracy, tool calls,
  tokens, tool errors and latency. Keep a held-out split. Compare runs with and without the gateway.
  - Oracles use the plain `sources` clients and pandas, never the gateway's services, so a
    gateway bug cannot grade itself correct.
  - Tune tool descriptions against `dev` only; run `held_out` to confirm, never to tune.
  - Read the transcripts in `evals/reports/*.json` before changing a tool: repeated calls or
    invalid-argument errors point at the description, not the model.
  - Evals spend money on OpenAI. `--oracles-only` and `--case <id>` keep iterations cheap.

---

## Workflow

- Plan → small change → tests → update docs in the same change.
- Releasing? Bump `version` in `pyproject.toml` and both `version` fields in `server.json`, and add
  a `CHANGELOG.md` entry. `tests/test_packaging.py` fails until all three agree. Re-record
  `docs/DEMO.md` (`scripts/demo.py`) when tool behaviour changes.
- Changed a tool's name, parameters, docstring or response model? Run
  `scripts/gen_tool_docs.py` and review the snapshot diff. `test_tool_docs.py` fails until you do.
- Validate new computations against an independent result (e.g. Project 1's archived runs)
  on identical inputs, and pin that agreement in a fixed-data test.
- Prompts and methodology notes may only name tools, series IDs and industry keys that exist;
  `tests/test_reference.py` checks this. Prompts describe *which tools to call and how to
  report*, never analysis the gateway performs (DESIGN.md decision 31).
- Run `ruff format`, `ruff check`, `pyright` and `pytest` before calling anything done.
- Manual check: `fastmcp dev inspector src/econ_gateway/server.py`.
