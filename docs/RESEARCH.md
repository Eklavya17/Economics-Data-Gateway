# Research notes: building an industry-grade economic-data MCP server

Collected 2026-11-07 before starting Project 2 (Economics Data Gateway). This file is the
reference for *why* the gateway is designed the way it is. `CLAUDE.md` holds the rules that
follow from it; `DESIGN.md` holds the architecture.

Items marked **(unverified)** came from a summarized part of a page and need a check against
the source before relying on them.

---

## 1. Sources read

| Source | What it is | Why it matters here |
|---|---|---|
| [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) (Sep 2025) | Tool-design and eval method for agent tools | Main source for tool shape, responses, errors and evals |
| [MCP spec: Tools](https://modelcontextprotocol.io/specification/latest/server/tools) | Protocol rules (latest spec: **2026-07-28**) | Tool fields, structured output, error types |
| [MCP spec: Security best practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices) | Security requirements | Input validation, rate limits, token handling, SSRF |
| [FastMCP docs](https://gofastmcp.com) / [PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp) | The Python framework we use. Latest stable **v4.1.6** (Nov 2026) | Exact APIs: tools, middleware, testing, running |
| [awslabs/mcp DESIGN_GUIDELINES.md](https://github.com/awslabs/mcp/blob/main/DESIGN_GUIDELINES.md) | AWS's rules for its ~60 Python MCP servers | Code layout, docstrings, logging, linting |
| [github/github-mcp-server](https://github.com/github/github-mcp-server) | GitHub's official MCP server (Go) | Toolsets, read-only mode, config patterns |
| [cyanheads/bls-labor-mcp-server](https://github.com/cyanheads/bls-labor-mcp-server) | BLS MCP server (TypeScript) | Best existing BLS design: offline catalog search, dataset handles |
| [stefanoamorelli/fred-mcp-server](https://github.com/stefanoamorelli/fred-mcp-server) | FRED MCP server (TypeScript) | Reliability layer: cache, token bucket, retries, merging duplicate requests |
| [cfdude/mcp-fred](https://github.com/cfdude/mcp-fred) | FRED MCP server (**Python, FastMCP**) | Closest stack match; large-output handling; tool tiers |
| [pipeworx-io/mcp-bls](https://github.com/pipeworx-io/mcp-bls) | Thin bridge to a hosted BLS gateway | Place-name → LAUS area resolution; anti-patterns |

Also in the project PDF: [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)
(reference servers).

---

## 2. Tool design (Anthropic article + MCP spec)

**Which tools to build**
- Don't wrap API endpoints one-to-one. Build fewer tools that each finish a real task.
  Example from the article: one `schedule_event` instead of `list_users` + `list_events` + `create_event`.
- Prefer **search** tools over **list everything** tools.
- Each tool has one distinct purpose. Overlapping tools confuse the model.
- Start with the tools your eval tasks need, then grow.

**Naming**
- Prefix tools by service or resource (`asana_search`, `asana_projects_search`). Prefix vs.
  suffix changes results, so test it.
- Unambiguous parameter names: `series_id`, not `id`; `start_date`, not `start`.
- MCP: names should be 1–128 chars, `[A-Za-z0-9_.-]`. AWS caps them at 64 chars including the
  server prefix and uses snake_case verb_noun.

**What to return**
- High-signal fields only (title, units, frequency, date, value). Drop low-level noise.
- Readable names beat cryptic IDs; the article reports fewer hallucinations when IDs were mapped
  to plain language. Still include IDs when a follow-up call needs them.
- Offer `response_format: "concise" | "detailed"`. The article's example: 72 vs 206 tokens.
- Claude Code caps tool responses at **25,000 tokens** by default. Use pagination, filters,
  truncation and sensible defaults. When you truncate, tell the agent how to narrow the query.

**Errors**
- No raw codes or tracebacks. Say what was wrong, what to change, and give an example of valid input.
- MCP spec: **tool execution errors** (bad input, upstream API failure) return a normal result
  with `isError: true` and actionable text, so the model can correct itself. **Protocol errors**
  (unknown tool, malformed request) are JSON-RPC errors.

**Descriptions**
- Write them like a briefing for a new hire: query formats, terms of art, how the tools relate
  and in what order to call them.
- Small description changes produced large score gains in Anthropic's tests.
- The article's example of a bad habit: Claude kept appending "2025" to search queries until the
  tool description told it not to. Our version of this risk is the model assuming its training
  year is the latest data year.
- Use annotations: `readOnlyHint`, `openWorldHint`, etc. Clients treat them as untrusted hints.

**Structured output (MCP spec)**
- `structuredContent` must conform to `outputSchema` when one is declared.
- For backward compatibility, also return the same JSON as a `TextContent` block.
- github-mcp-server only sends structured output to clients on protocol 2026-07-28 or newer;
  older clients get text **(unverified)**.

---

## 3. Evaluation method (Anthropic article)

1. Prototype quickly (local server + Claude Code), then build a real eval before every round of changes.
2. Generate **many realistic tasks**. Good tasks can take dozens of tool calls.
3. Each task gets a **verifiable outcome**: exact match or an LLM judge. Don't fail correct
   answers over formatting.
4. Optionally record which tools a task is expected to use, without overfitting to them.
5. Run each task as its own agent loop via the API. Ask the agent to output reasoning and
   feedback before tool calls.
6. Metrics: **accuracy, runtime per call and per task, number of tool calls, total tokens, tool errors.**
7. Read the transcripts. Redundant calls mean pagination or limits are wrong. Many
   invalid-parameter errors mean the description needs work.
8. Keep a **held-out test set** so the tools aren't overfit to the eval.
9. Paste eval transcripts into Claude Code to refactor tools consistently.

This fits Project 1's eval design (live oracles, graded dimensions, `--repeat` for consistency),
which we reuse.

---

## 4. Existing BLS/FRED MCP servers: what to borrow and what to avoid

| Repo | Tools | Strongest idea | Weakness |
|---|---|---|---|
| cyanheads BLS | 6–7: `bls_list_surveys`, `bls_search_series`, `bls_get_series`, `bls_get_latest`, dataframe describe/query | **Offline catalog** (BLS LABSTAT flat files in SQLite FTS5) means search costs no API quota. Large results go to a DuckDB "canvas" behind a handle with an expiry. Batch calls report `unprocessedSeriesIds` / `failed[]`; each observation has an `available` flag for BLS's `-` value. | TypeScript; framework-specific |
| stefanoamorelli FRED | 3: `fred_browse`, `fred_search`, `fred_get_series` (with FRED's `units` transforms) | **Reliability layer**: LRU cache (TTL), token bucket at 120/min, retries on 429/5xx with backoff, jitter and `Retry-After`, merging of identical in-flight requests, `/healthz` | Only `limit`/`offset`; no output-size cap or truncation notice |
| cfdude FRED (Python/FastMCP) | ~40 in tiers, many hidden until `activate_*_tools()` | **Output sized to the context**: inline under ~50K tokens, else save to CSV, else background job. `servers/` per domain, `api/` client, `utils/` (token estimator, output handler). pytest with an 80% coverage target, ruff. | Tool count too high; README counts don't match the tool list |
| pipeworx BLS | 6 incl. `bls_local_unemployment`, `bls_popular_series` | **Place-name → LAUS area** via a bundled crosswalk; returns candidates when a name is ambiguous; falls back to the county and says so; states data vintage | **API key passed as a tool argument** (`_apiKey`) puts it in model context and logs; logic lives in a closed hosted gateway |

**Borrow**
- Free, offline discovery from a local catalog. Project 1's 295-series catalog + BM25 resolver
  already does this. Extend it with LABSTAT series lists later.
- Per-item success/failure lists for batch fetches.
- An explicit flag for missing or preliminary values.
- Handles or files for large results instead of inlining them.
- Token bucket + backoff with `Retry-After` + merging of identical requests + `/healthz`.
- Place-name resolution with candidates for ambiguous names; always state the vintage and
  reference period.
- Small tool surface (cyanheads ~6, FRED ~3). Target **≤ 12** tools for us.

**Avoid**
- API keys as tool parameters. Keys come only from server environment variables.
- Documentation that drifts from the code. Generate the tool list in the README from the
  server, or test that they match.
- Unbounded responses with no truncation notice.

---

## 5. Engineering conventions from awslabs/mcp

Adapted rules (AWS-specific parts dropped):
- **Layout:** `server.py` (FastMCP app + `main()` entry point; no separate `main.py`),
  `models.py` (Pydantic), `consts.py` (constants), extra modules for API clients.
- **Entry point:** `[project.scripts]` → `package.server:main`. `__version__` in `__init__.py`.
- **Tools:** `async def`, explicit `name=`, params as `Annotated[T, Field(description=...)]`,
  `Literal[...]` for fixed choices, `ge`/`le`/`min_length` bounds, `ctx: Context` when needed.
- **Tool docstrings:** purpose, then `## Usage`, `## Tips`, and `## Output format` sections.
  Say which tool to call first. Put directives for the model last, marked `IMPORTANT:` with a reason.
- **Server `instructions=`:** a one-line description of every tool and the order to use them in.
- **Pydantic:** every model has a docstring with an `Attributes:` section; str-Enums for fixed
  values; `field_validator` / `model_validator` for checks.
- **Constants:** UPPER_CASE in `consts.py`, each with a comment giving its units
  (`API_TIMEOUT = 30  # seconds`).
- **Logging:** to **stderr only**. Level from an environment variable, default WARNING. Log
  calls to external services with context.
- **Errors:** log, send an error to the client, return an error result; separate client
  mistakes from server failures; never leak secrets in messages.
- **Lint:** `ruff format` + `ruff check` (E, F, I, B plus more) + `pyright`, run by pre-commit.
- **README:** setup, every tool and resource with input/output examples, every environment
  variable, limitations.

---

## 6. FastMCP 4 API reference (what we will use)

From gofastmcp.com, FastMCP **v4.1.6**. Pin an exact version in `pyproject.toml`.

```python
from typing import Annotated
from pydantic import Field
from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations          # (unverified import path)

mcp = FastMCP("econ-gateway", instructions="...", mask_error_details=True)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def get_series(
    series_id: Annotated[str, Field(description="BLS or FRED series ID, e.g. 'CUSR0000SA0'")],
    ctx: Context,
) -> SeriesResponse:                            # Pydantic return → structuredContent + outputSchema
    ...
```

- **Parameter descriptions:** `Annotated[..., Field(description=...)]` takes precedence over
  docstring `Args:`.
- **Structured output:** a Pydantic/dict return type creates `outputSchema` and
  `structuredContent` automatically. `ToolResult(content=..., structured_content=..., meta=...)`
  gives full control.
- **Annotation defaults:** readOnly=false, destructive=true, idempotent=false, openWorld=true,
  so we must set them explicitly **(unverified)**.
- **Errors:** `ToolError` messages always reach the client. With `mask_error_details=True`,
  other exceptions show a generic message.
- **Context:** `await ctx.info()` / `ctx.warning()`, `await ctx.report_progress(progress=, total=)`.
  For elicitation on the 2026-07-28 protocol, return `InputRequiredResult` and read
  `ctx.input_responses`; `ctx.elicit()` only works on older connections.
- **Resources:** `@mcp.resource("econ://series/{series_id}", mime_type="application/json")`.
  Templates screen for path traversal by default.
- **Prompts:** `@mcp.prompt` returning `list[Message]` (`from fastmcp.prompts import Message`).
- **Middleware**, added in order with `mcp.add_middleware(...)`:
  - `fastmcp.server.middleware.error_handling.ErrorHandlingMiddleware`, `RetryMiddleware`
  - `fastmcp.server.middleware.rate_limiting.RateLimitingMiddleware(max_requests_per_second=, burst_capacity=)`
  - `fastmcp.server.middleware.caching.ResponseCachingMiddleware`
  - `fastmcp.server.middleware.logging.StructuredLoggingMiddleware`
  - `fastmcp.server.middleware.timing.TimingMiddleware`
  - `fastmcp.server.middleware.response_limiting`
- **Composition:** `main.mount(sub_server, namespace="bls")` prefixes tools as `bls_<name>`.
  This gives us toolsets.
- **Testing:** `async with Client(transport=mcp) as c: await c.call_tool("name", {...})`
  runs fully in memory. Set `asyncio_mode = "auto"` with pytest-asyncio. The docs also suggest
  inline-snapshot for schema snapshots.
- **Running:**
  - `mcp.run()` uses stdio.
  - `mcp.run(transport="http", host=, port=)` serves at `/mcp`.
  - `fastmcp dev inspector server.py` opens MCP Inspector.
  - `fastmcp install claude-desktop fastmcp.json` installs into Claude Desktop.
  - `fastmcp.json` declares the source, environment and deployment.
- **HTTP auth:** `JWTVerifier(jwks_uri=, issuer=, audience=)` from
  `fastmcp.server.auth.providers.jwt`; `StaticTokenVerifier` is for development only.

---

## 7. Security requirements (MCP spec) for a read-only data server

- **MUST** validate every tool input. **MUST** rate-limit tool calls. **SHOULD** sanitize outputs.
- **MUST NOT** accept tokens that were not issued for this server (no token passthrough).
  BLS/FRED keys live on the server.
- **SSRF:** never fetch a URL supplied by the client or model. Upstream hosts are a fixed
  allow-list (`api.bls.gov`, `api.stlouisfed.org`, `data.bls.gov`, `www.bls.gov`), HTTPS only.
- **Local HTTP mode:** bind to 127.0.0.1 and require a token (protects against DNS rebinding).
  Prefer stdio for local use.
- **State handles** (e.g. dataset handles) must be random, expiring, and tied to the caller
  when auth is on. Never treat a handle as proof of identity.
- Treat upstream text (footnotes, titles) as data, not instructions.

---

## 8. Production patterns from github-mcp-server

- **Toolsets**: groups of tools enabled with `--toolsets` / an environment variable. The README
  says enabling only what you need helps tool choice and saves context. Our version is mounted
  sub-servers such as `core`, `wages`, `regional` and `analyst`, chosen by `ECON_TOOLSETS`.
- **Read-only mode** overrides everything. We are read-only throughout, but the optional
  `ask_labor_analyst` (which runs code) gets its own flag.
- **Description overrides** via a config file or environment variables, so descriptions can
  be tuned without code changes. Useful for eval-driven tuning.
- **Renamed tools** keep their old names as aliases; an invalid tool name stops startup.
- **HTTPS required** for upstream hosts (loopback excepted).

---

## 9. Decisions this research led to

1. **Python + FastMCP 4 (pinned)**, reusing Project 1's `labor_agent.data` / `labor_agent.resolve`
   (sync, `requests`). Blocking calls run in a thread (`anyio.to_thread.run_sync`) inside async tools.
2. **≤ 12 tools**, grouped into toolsets with `mount(namespace=...)`: discovery → retrieval →
   computation. Every tool is read-only and has annotations set.
3. **Every response is a Pydantic model** with a `provenance` block (source, series_id, URL,
   units, seasonal adjustment, reference period, retrieved_at, preliminary/stale flags) and a
   short text summary.
4. **Small responses by default**: `max_points`, `response_format`, explicit `truncated` plus a
   hint on how to narrow. Large data goes to a dataset handle or a resource instead of inline.
5. **Errors tell the agent what to call next**: `ToolError` with the next tool, an example
   argument, and valid options.
6. **Reliability**: caching tied to the release calendar, a token bucket per upstream, backoff
   honoring `Retry-After`, merging identical requests, a circuit breaker with stale fallback,
   and BLS quota tracking.
7. **Keys from the environment only**, an upstream host allow-list, logs to stderr only, and
   `mask_error_details=True`.
8. **Evals follow the Anthropic method + Project 1's oracles**: realistic tasks, verifiable
   answers, metrics (accuracy, tool calls, tokens, errors, latency), a held-out split, and a
   with/without-gateway comparison.
