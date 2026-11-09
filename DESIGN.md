# Economics Data Gateway — Design

An MCP server that lets any AI assistant (Claude Desktop, Claude Code, Cursor, custom agents)
pull **live, official, cited** U.S. economic data from BLS and FRED.

- Rules for writing code here: `CLAUDE.md`.
- Sources and the reasoning behind this design: `docs/RESEARCH.md`.
- This file: architecture, the tool surface, phases, and a log of decisions.

---

## 1. Goal and demo

> In Claude, ask *"What did wages look like for registered nurses in Texas last year?"* and
> get real BLS figures fetched at that moment, with the source cited.

**Success means**
- the client model answers economic questions using numbers from this server and nothing else;
- it reaches the right series in **≤ 3 tool calls**;
- every number can be traced to a series ID, period and URL.

## 2. Architecture

```
 MCP clients ──stdio / Streamable HTTP──▶ FastMCP server (econ_gateway.server)
                                           │  instructions · middleware (errors, timing, logging, rate limit)
                                           ▼
                                  toolsets/  (thin: validate → call service → shape response)
                                           ▼
                                  services/  (business logic, no MCP imports, unit-tested)
                                           ▼
                labor_agent.data / labor_agent.resolve   (Project 1: BLS, FRED, OEWS, QCEW clients,
                                                          disk cache, 295-series catalog, BM25 retrieval)
```

- **Layers:** tools only handle MCP concerns (schemas, errors, annotations). Services hold the
  economics. Project 1's clients hold HTTP, caching and parsing.
- **Reliability (Phase 4):** Project 1's clients take a `session` and a `cache`. The gateway
  passes a session whose transport adapter enforces the allow-list, rate limits, BLS quota and
  circuit breaker (`infra/http.py`), and a `DiskCache` subclass whose entries expire on the
  release calendar (`infra/cache.py`). No Project 1 code changed.
- **Sync tools:** Project 1's clients use `requests`, so services and tools are synchronous.
  FastMCP 4 runs sync tools in a worker thread by default (`run_in_thread=True`), so the
  event loop is never blocked. No hand-written thread offloading is needed (decision 9).

## 3. Tool surface (cap: 12 tools)

| Toolset | Tool | Phase | Purpose |
|---|---|---|---|
| core | `search_series` | 1 | Topic + optional place → ranked candidate series IDs (catalog first, then FRED search) |
| core | `describe_series` | 1 | Full metadata for one ID: title, units, frequency, SA/NSA, coverage, notes, URL |
| core | `get_series` | 1 | Observations for 1–10 IDs, optional transform (YoY %, period %, change), capped size |
| core | `get_latest` | 1 | Latest value + previous period + year-ago change for 1–20 IDs |
| core | `find_area` | 2 | Place name → `area_id` (`us`, `state:48`, `metro:12420`, `county:48453`) plus its LAUS unemployment series IDs |
| wages | `find_occupation` | 2 | Occupation text → SOC codes |
| wages | `get_occupation_wages` | 2 | OEWS wages by occupation × area, or ranked across all states/metros (the demo) |
| regional | `get_area_employment` | 2 | QCEW jobs, establishments and weekly wages by area × industry |
| compute | `real_wage_growth` | 3 | A wage series deflated by CPI-U, CPI-W or PCE: nominal, inflation and real growth per period, plus the change over the window |
| compute | `compare_series` | 3 | Rank 2–25 series on one measure (latest value, year-ago change, change since a date), with an optional benchmark such as CPI |

~~`ask_labor_analyst`~~ was dropped (decision 12): it would make the gateway a wrapper around Project 1.

**Resources and prompts (Phase 5).** These do not count toward the tool cap: resources are
documents a client attaches, prompts are templates a user picks (decision 29).

| Kind | Name | Purpose |
|---|---|---|
| resource | `econ://catalog`, `econ://catalog/{category}` | The curated catalog, whole or by topic |
| resource | `econ://series/{series_id}` | `describe_series` + latest value for one ID |
| resource | `econ://releases` | Last and next dates of the headline releases |
| resource | `econ://methodology`, `econ://methodology/{topic}` | Explainers: seasonal adjustment, revisions, OEWS, QCEW, LAUS, real wages, rates, freshness, area IDs |
| resource | `econ://status` | Version, toolsets, key presence, breaker/rate-limit/quota state |
| prompt | `occupation_wages`, `labor_market_snapshot`, `local_economy`, `real_wage_check` | Question templates that name the tools to call and the reporting rules |

Jobless claims, JOLTS, GDP and rates are reachable in Phase 1 through
`search_series` → `get_series`/`get_latest`. They only get dedicated tools if evals show
agents struggling without them.

### Shared response contract

- Every tool returns a Pydantic model (which produces `outputSchema` and `structuredContent`)
  plus a one-line text summary.
- Each series in a response has a `SeriesMeta`:
  - `series_id`, `source`, `title`, `units`, `frequency`, `seasonal_adjustment`
  - `url`: `data.bls.gov/timeseries/<id>` or `fred.stlouisfed.org/series/<id>`
- Each series also carries provenance: `retrieved_at` and `latest_available`.
- Observations carry `preliminary` (from BLS footnotes).
- Batch tools return `results[]` + `failed[]`. One bad ID never fails the call.
- Size limits:
  - `max_points` per series (default 60, max 500);
  - a total cap across series;
  - `truncated: true` + `hint` whenever data was cut.
- `response_format`: `concise` (default) or `detailed` (adds footnotes, notes, period names).

## 4. Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Scaffold: package, config, logging, server + `main()`, tooling (ruff, pyright, pytest) | done |
| 1 | Core toolset: `search_series`, `describe_series`, `get_series`, `get_latest` over BLS + FRED | done (see §6) |
| 2 | Wages + regional toolsets (OEWS, QCEW, LAUS place resolution) → PDF demo | done (see §7) |
| 3 | Compute toolset; generated README tool docs; schema snapshot tests | done (see §8) |
| 4 | Reliability: release-calendar cache TTLs, per-upstream token bucket, merging identical requests, circuit breaker with stale fallback, BLS quota tracking | done (see §9) |
| 5 | Resources (`econ://catalog`, `econ://series/{id}`, methodology), prompts | done (see §10) |
| 6 | Reference client + agentic evals (accuracy, tool calls, tokens, errors; held-out split; with vs. without gateway) | done (see §11) |
| 7 | Streamable HTTP, JWT auth, Docker, `/health`, OpenTelemetry | done (see §12) |
| 8 | README polish, demo recording, optional MCP registry listing | done (see §13); registry listing not yet published |

## 5. Decision log

Newest last. Each entry: what we decided, and why.

1. **Python + FastMCP 4.1.6, pinned exactly.** Same language as Project 1, so its data layer is
   reused. FastMCP 4 gives structured output from Pydantic, middleware, in-memory testing and mounting.
2. **Import Project 1 (editable install), don't copy it.** One tested data layer.
   - Project 1's `labor_agent.config` loads its own `.env` at import time and never overrides
     variables that are already set.
   - The gateway therefore loads its own `.env` *first*, so gateway values win.
   - Keys left only in Project 1's `.env` still work.
3. **Phase 1 transforms live in the gateway, not `labor_agent.analysis`.** Importing any
   `labor_agent.analysis` submodule runs that package's `__init__`, which imports plotly (an
   optional extra of Project 1). The three Phase 1 transforms (YoY %, period %, absolute change)
   are small, so they are written in `services/transforms.py` with date-based lags matching
   Project 1's semantics. *Revisit in Phase 3:* make Project 1's `analysis/__init__` lazy so the
   gateway can import `metrics` directly for `deflate` / `real_growth`.
4. **`search_series` tries the catalog first, then FRED search.** The catalog is free (no API
   quota), validated, and carries facets. FRED search (800k+ series) is added when the catalog
   has fewer than 3 hits, or when the query names a place none of the catalog hits cover
   (e.g. "average hourly earnings Texas" → the catalog only has the national series).
5. **IDs in, IDs out.** Retrieval tools take series IDs only. Agents get IDs from `search_series`,
   so there is one discovery path, and every "not found" error points back to it.
6. **No current-year assumptions.** Default windows are computed on the server from today's date.
   Responses always state `latest_available`, so a model with stale training data sees the real
   latest period.
7. **`ErrorHandlingMiddleware(transform_errors=False)`.** With the default (`True`), FastMCP
   turns a `ToolError` into a JSON-RPC "Internal error". That hides our actionable message and
   breaks the MCP rule that tool failures come back as results with `isError: true`. An
   in-memory test caught this. We keep the middleware for its error logging and counts.
8. **Re-rank FRED search results in the gateway.** Live check: for "average hourly earnings
   Texas", FRED's popularity order put *Construction* first (tied with *Total Private*), and
   `search_rank` put *Total Nonfarm employment* first. The gateway sorts by:
   1. requested words found in the title;
   2. fewest unrequested qualifiers;
   3. sub-annual before annual;
   4. popularity.

   Rules are in `services/search.py`, and the real tie is reproduced in `tests/conftest.py`.
9. **Sync tools, no manual thread offloading.** This replaces the original plan of
   `anyio.to_thread` (§2). FastMCP 4's `@tool(run_in_thread=True)` default does the same job
   with less code.
10. **Explain data gaps.** Live check: the October 2025 unemployment rate does not exist
    (the federal shutdown stopped the household survey), so the year-ago comparison was a silent
    `null`. `get_latest` now always returns `year_ago_period` and adds a `notes` entry when its
    value is missing.
11. **Malformed FRED IDs are "not found", not "upstream failure".** FRED answers
    "Invalid value for variable series_id" for IDs like `NOT_A_SERIES`. That used to produce a
    "Retry shortly" hint; it now points to `search_series`.

12. **The gateway stays separate from Project 1.** Project 1 is an *analyst*: it owns an LLM,
    plans, writes and runs code, draws charts, writes and verifies briefings, and has a UI. The
    gateway is *infrastructure*: it has no LLM, runs no generated code, writes no prose, and
    serves deterministic, cited data over a standard protocol to whichever model connects.
    - Shared on purpose: Project 1's data plumbing (`labor_agent.data`, catalog retrieval,
      tokenizer, OEWS/area matchers).
    - Never used: its agent, LLM layer, sandbox, analysis recipes, charts, writer, verifier.
    - A Phase 5 `ask_labor_analyst` tool would have broken this line, so it was dropped. If
      the projects should combine later, Project 1 can *consume* the gateway as an MCP client.
13. **One `area_id` across OEWS, QCEW and LAUS.** The three programs code places differently:
    - Texas: OEWS `48`, QCEW `48000`, LAUS `ST4800000000000`.
    - Austin metro: OEWS `12420`, QCEW `C1242`.

    `find_area` returns one readable ID (`state:48`, `metro:12420`, `county:48453`) and the
    tools translate it. It also builds the LAUS unemployment series IDs, so "unemployment rate
    in Austin" takes two calls: `find_area` → `get_latest`. LAUS ID formats were checked live:
    - state: `LASST…`, seasonally adjusted;
    - metro: `LAUMT…`, NSA only (the `LASMT…` form returned no data);
    - county: `LAUCN…`.
14. **OEWS: latest year found by trying, never assumed.** OEWS covers May of each year, released
    the following spring. The gateway tries last year, then the year before, and remembers the
    result for a day.
    - Wages come only from cross-industry rows.
    - Top-coded cells (`#` in the source) are reported as `top_coded` and never as a number.
    - Counties are rejected with a pointer to their metro, because OEWS does not publish counties.
15. **QCEW: never report suppressed cells as zero.** QCEW publishes `0` with disclosure code `N`
    for confidential cells. Those come back as `disclosed: false` with null values.
    - Totals across ownership exist only for "all industries". Sector rows are private
      ownership, and the response says so.
    - The latest quarter is found by stepping back from today (it was 2026 Q1 on 2026-11-07).

16. **Two fixes from live data, each with a regression test:**
    - **Duplicate areas.** QCEW's area reference table repeats ~4,600 codes, so "Austin"
      listed Austin County twice. The joined area table now keeps one row per `area_id`.
    - **City names should mean the metro.** Project 1's matcher scored *Austin County* (3.23)
      above the *Austin metro* (3.16). A +0.3 metro prior fixes this. An explicit "Austin
      County" query still wins through its +2 phrase-match bonus.
    - **LAUS units.** BLS metadata gives LAUS units as "unemployment rate", which the
      percent-only rate check missed, so a metro rate got a "−5.1%" change. `is_rate` now
      accepts the word "rate", and BLS rate units are reported as `percent`.
17. **`FailedItem.series_id` renamed to `id`.** It now also carries areas
    (`county:48453`) and occupation×area pairs (`25-2021@metro:12420`). This changed the
    Phase 1 output schema; no client depended on it yet.

18. **Compute tools are deterministic arithmetic, written in the gateway.** This supersedes
    decision 3's "revisit": importing Project 1's `analysis` package would reuse exactly what
    decision 12 keeps out.
    - The tools answer common multi-step questions in one call, so the model never does the
      arithmetic itself: "are real wages keeping up?" and "which industries gained most?".
    - They return numbers and flags only; no prose and no charts.
19. **Real wage method (`real_wage_growth`):**
    - Real growth is exact: `(1 + nominal) / (1 + inflation) − 1`, not the
      `nominal − inflation` approximation.
    - The deflator's seasonal adjustment follows the wage series: SA wages use
      `CUSR0000SA0` / `CWSR0000SA0`, NSA wages (state series, ECI) use `CUUR0000SA0` /
      `CWUR0000SA0`. PCE (`PCEPI`) exists only SA, so pairing it with NSA wages adds a note.
    - Frequencies are aligned by averaging the price index over complete periods. Monthly CPI
      becomes a quarterly mean for the ECI; a quarter with fewer than 3 months is dropped.
    - Real levels are in dollars of the latest common period.
    - Rates (percent units) are rejected, because a rate is not a wage level.
20. **"Since YEAR" means that year's average (`compare_series`).** This is the convention
    Project 1's evals settled on; "since 2019-06" means that period's value. When series end
    in different periods, the response flags it rather than hiding it.
21. **Generated tool docs and schema snapshots.**
    - `scripts/gen_tool_docs.py` renders the README tool table and `docs/TOOLS.md` from the
      live server.
    - It also writes `tests/snapshots/tool_schemas.json`: every tool's name, description,
      input schema and output schema.
    - A test fails if any of the three drifts from the code, so interface changes are
      deliberate and visible in review. This replaces the hand-written parameter list in
      `test_server.py`.
22. **With a benchmark, every series is evaluated at the benchmark's latest period.**
    Release calendars differ: the jobs report comes out about a week before CPI. Without
    this, `gap_vs_benchmark` subtracts growth rates of different months. Series with newer
    data are trimmed to the benchmark's period, and `notes` says so and points to
    `get_latest` for the newer values.
23. **Guards live in the transport adapter of the session handed to Project 1's clients.**
    - `BLSClient` and `FREDClient` accept `session=`. A `GuardedAdapter` mounted on that session
      sees every request they make, including each 50-series BLS batch, without changing
      Project 1.
    - Order: allow-list → BLS quota → token bucket → circuit breaker → timeout cap → send.
      Quota is spent only once the breaker admits the request.
    - urllib3 retries run inside the wrapped adapter, so a retried request costs one token and
      the breaker sees only its final outcome.
    - Failures for the breaker are network errors, timeouts, 429 and 5xx. A 400 ("series does
      not exist") means the upstream is healthy.
    - Timeouts are capped at 5 s connect / 30 s read with one retry, so the worst case (~71 s)
      fits inside the 90 s tool timeout. Project 1's defaults (60 s, 3 retries) did not.
    - Guard errors subclass `requests.RequestException`, so every existing network-error path
      handles them; the upstream layer reads their `advice` for the message.
24. **Cache entries expire on the release calendar, not after 12 h.**
    - FRED's `release/dates` gives past and scheduled dates for every release, BLS releases
      included. A BLS series maps to its release by ID prefix (FRED does not know BLS IDs);
      a FRED series by `series/release`. The IDs were checked live (UNRATE → 50, CPIAUCSL → 10,
      TXUR → 112, AUST448URN → 113).
    - Rule: stale if fetched before the latest release instant (date + 8:30 ET); kept 10 min if
      fetched within 3 h after it (FRED posting lag, 10:00 releases); otherwise fresh.
      Unknown release → 12 h; everything is refetched after 7 days.
    - It subclasses Project 1's `DiskCache`, so the files stay shared with Project 1. It reads
      the entry's `fetched_at` through `DiskCache._paths`, a private helper; if Project 1
      changes its file layout, `test_reliability.py` fails.
    - Calendar lookups are cached (12 h per release, 7 days per series) and their failures fall
      back to the 12 h TTL. Freshness never fails a data request.
25. **Stale fallback: an old answer, clearly labelled, beats an error.**
    - When BLS or FRED fails (network, 5xx, open breaker, rate limit, quota), the last cached
      copy of each series is read offline (a session whose adapter never sends) and tagged
      with the time it was fetched.
    - The tag travels as an optional `stale_as_of` column, then `RawSeries.stale_as_of`, then
      `SeriesMeta.stale_as_of`, plus a note in every response that contains stale data.
    - BLS falls back per series, so one uncached series does not hide the others. "Series not
      found" never falls back.
26. **BLS quota: count locally, persist, and believe BLS.**
    - Counted per request at the adapter; persisted in `ECON_STATE_DIR/bls_quota.json` because
      Claude Desktop restarts stdio servers often.
    - Reset at midnight US Eastern (BLS does not document it). A BLS "daily threshold" refusal
      marks the day exhausted even if the local count disagrees (the key is shared with
      Project 1).
    - Optional metadata lookups stop at a reserve of 25 queries (5 without a key), keeping the
      rest for data.
    - OEWS and QCEW files come from Project 1 code that builds its own sessions, so they are
      outside the guards. They are cached for a year and 30 days respectively. Guarding them
      would mean changing Project 1; revisit if evals show problems.
27. **Merge identical concurrent requests (single-flight).** Agents issue parallel tool calls
    that overlap (CPI for `real_wage_growth` and `compare_series`). Keys are
    `(source, ids, window)`; followers wait for the leader and receive copies of its frame.
    Nothing is kept after the call; caching is the disk cache's job.
28. **Rate limits are self-imposed and conservative:** BLS 2/s (burst 5), FRED 1.5/s (burst 10;
    FRED allows 120/min). A request waits up to 10 s for a token, then fails with "retry
    shortly", well before the tool timeout.
29. **Reference material goes in resources; numbers still come from tools.**
    - In MCP, tools are model-controlled and resources are application-controlled: the client or
      user decides what to attach. Catalog browsing, methodology and status fit that; they do
      not need to be in the model's tool list, and adding them as tools would crowd the 12-tool cap.
    - Resources reuse the tool services (`econ://series/{id}` is `describe_series` +
      `get_latest`), so there is no second data path to test or keep consistent.
    - Expected failures (unknown category or topic, malformed ID) raise `ResourceError`, whose
      message reaches the client despite `mask_error_details`, listing the valid values.
30. **The release schedule is visible to the model through `describe_series`**, not only in
    `econ://releases`. "Is newer data coming?" is a question agents ask, and most clients do not
    let the model read resources by itself. `release` is advisory: if the calendar is
    unavailable it is `null`, never an error.
31. **Prompts spell out tool workflows and reporting rules, nothing more.**
    - The client's model writes the answer; the gateway still generates no analysis or prose
      (decision 12). Each prompt names the tools, the order, and what to report (periods,
      preliminary values, percentage points, citations).
    - A prompt is registered only when the toolsets it uses are mounted, so it never sends the
      model to a tool it cannot see.
    - Tests check that every tool named in a prompt or methodology note exists, that hard-coded
      series IDs are in the real catalog, and that QCEW industry keys are valid, so the text
      cannot drift from the code.
32. **Generated docs cover resources and prompts.** `scripts/gen_tool_docs.py` also renders the
    README resource and prompt tables, adds both to `docs/TOOLS.md`, and writes
    `tests/snapshots/reference_surface.json`. The same staleness test covers them.
33. **Resource bodies are compact JSON without null fields.** The reader is a model. With
    indentation the full catalog was ~80K characters (~20K tokens); compact, and with U.S.
    geography omitted, it is ~60K (~15K tokens). Its description points to
    `econ://catalog/{category}` and `search_series` for narrower reads.
34. **Methodology notes are Markdown shipped in the package** (`econ_gateway/methodology/`,
    declared as package data). Each states agency practice in general terms (e.g. "CES
    estimates are revised in the next two monthly releases") and then how the gateway reports
    it. A test requires a title and a summary paragraph for the index.
35. **The eval client is provider-agnostic, over the OpenAI-compatible Chat Completions API.**
    - Live runs use OpenAI (`gpt-4.1-mini`, the key Project 1 already has); the same driver points at
      a local Ollama server (`--provider ollama`). This was the user's choice: no Claude key is
      configured on this machine. The client behaves like an MCP host, so results carry over.
    - Like Claude Desktop, it connects over stdio, puts the server's `instructions` in the system
      prompt, offers every tool with its full description and input schema, passes results back as
      compact JSON of `structuredContent`, and passes tool errors back as text.
    - It lives in `evals/`, outside the server package. The server still contains no LLM.
36. **Oracles never use the gateway's services.** They compute expected answers from Project 1's
    `get_series` / `load_oews` / `fetch_qcew` and FRED's release calendar over plain HTTP, with plain
    pandas. A bug in `get_latest` or `real_wage_growth` therefore fails the eval instead of grading
    itself correct. Oracles run before any LLM call; a failing oracle is reported as such.
37. **Deterministic grading, lenient on format, strict on facts.** Numbers are extracted with
    scale words ("159.3 million", "931K") and matched within a tolerance; periods match any common
    spelling; abstention cases need a "not available" phrase. No LLM judge: grades are reproducible
    and free. The cost is that grading cannot judge reasoning quality beyond these checks.
38. **Cases test honesty as well as lookup.** Besides national, state, metro, county, occupation,
    real-wage, comparison and calendar questions, one case asks about a month that does not exist
    yet, one asks for county OEWS data (not published: the right answer offers the metro area), and
    one hits a real gap (October 2025 was never published, so a year-over-year change must say so).
39. **Baseline mode runs the same questions with no tools.** The difference in accuracy is what the
    gateway adds. A good baseline answer abstains; a confident wrong number is the failure mode the
    gateway exists to prevent.
40. **Loop budgets: 10 LLM turns and 16 tool calls per question.** When either is spent, the client
    answers outstanding tool calls with a "budget used up" message and makes one last call with
    `tool_choice="none"`. Forced stops are counted in the report, because they signal a tool that
    returns too little or the wrong thing.
41. **Unpublished periods are named, with their nearest published neighbours** (from eval round 2).
    - Agencies sometimes skip a period entirely (October 2025: no household-survey or CPI data,
      federal shutdown). A bare `null` led the eval model to compare with a nearby month without
      saying so, or to label September's value as "October 2025".
    - `get_latest`: when the year-ago period is missing, `notes` names it, gives the nearest
      published periods with their values, and says never to present a neighbour under the missing
      label. The year-ago fields stay null: the gateway still never fills in data.
    - `get_series`: `notes` names unpublished periods inside the window, and changes that are null
      because their comparison period is missing (`services/gaps.py`).
    - `get_series` with an `end_date` within the last 2 years fetches through today anyway (same BLS
      queries), so `latest_available` is always known and a note says when `end_date` cut off newer
      data. Models guess "the latest month" from today's date; this catches a wrong guess.
    - Tool docstrings and server instructions say the same, and `get_latest` is named as the tool
      for "change over the past year".
42. **List settings are parsed as comma-separated text (`NoDecode`).** pydantic-settings tried to
    JSON-decode `ECON_TOOLSETS=core,wages` before the CSV validator ran, so the documented format
    crashed at startup. Found while adding the HTTP list settings; a test now covers it.
43. **HTTP auth is JWT verification only, with issuer and audience mandatory.**
    - The gateway is a resource server: it verifies bearer tokens from an existing identity
      provider (JWKS URL, PEM key or key file) and does not issue tokens or run OAuth flows.
    - `iss` and `aud` are required settings because the MCP spec forbids accepting tokens minted
      for other services (token passthrough). JWKS must be HTTPS, except on localhost.
    - The JWKS URL is operator configuration, not client input, so it does not break the
      upstream allow-list rule (CLAUDE.md rule 7).
44. **The server refuses an open endpoint on the network.** HTTP on a non-loopback address with
    auth off stops at startup, unless `ECON_ALLOW_UNAUTHENTICATED_REMOTE=true` says a proxy
    authenticates. The Docker image binds 0.0.0.0, so it cannot start unprotected by accident.
45. **Host/Origin checks in strict mode on every request.** FastMCP leaves them off by default.
    Loopback names are always allowed; public names must be listed in `ECON_ALLOWED_HOSTS`. This
    is the DNS-rebinding protection the MCP spec asks for.
46. **Probes: `/health` is liveness, `/ready` is readiness, both without auth.** Readiness depends
    on the local catalog only. An open upstream circuit reports `degraded` but stays 200: cached
    data still flows, and pulling every replica because BLS is down would turn a partial outage
    into a full one. Bodies contain no keys, quotas or request details.
47. **Stateless HTTP by default.** The tools need no session state (no progress or elicitation),
    so each request stands alone and replicas can run behind a load balancer.
48. **Tracing: FastMCP's spans plus the gateway's own, SDK optional.** `ECON_TELEMETRY=otlp` uses
    the standard `OTEL_EXPORTER_OTLP_*` variables. `console` writes to stderr, because stdout is
    the stdio protocol stream (a test runs it in a subprocess and checks stdout stays empty).
    Spans record upstream name, status, guard rejections and stale fallbacks, never URLs, because
    FRED's query string holds the API key.
49. **Docker: built from `ECO_Projects`, secrets at run time, hardened compose.** The image
    installs Project 1 editable (its reference tables stay where its code expects them), copies
    named folders only (plus a Dockerfile-specific ignore file), runs as a non-root user, and
    writes only to the `/data` volume. Compose adds a read-only root filesystem, drops all
    capabilities, sets `no-new-privileges`, and publishes on loopback.
50. **The demo is a recording of a real run, not a mock-up.** `scripts/demo.py` sends four
    questions through the eval reference client, with the gateway over stdio and live data. It
    writes every tool call, a result excerpt and the answer to `docs/DEMO.md`. It is text rather
    than video so it can be diffed, quoted in the README and re-recorded with one command. The
    transcript names the model (`gpt-4.1-mini`, not Claude) so readers know what produced it.
51. **The README is a front page; reference material lives in `docs/`.** The README keeps the
    pitch, a real demo excerpt, the quick start, the generated tool, resource and prompt tables,
    the data rules and the eval results. Deployment, sample responses, development and publishing
    moved to `docs/DEPLOYMENT.md`, `EXAMPLES.md`, `DEVELOPMENT.md` and `PUBLISHING.md`. A test
    checks that every relative link in the docs resolves.
52. **Version 1.0.0, from a single source.** `__version__` reads the installed package metadata
    (`importlib.metadata`), so `pyproject.toml` is the only place the number is written. A test
    checks that `server.json` and `CHANGELOG.md` agree with it.
53. **MCP Registry: metadata ready, publishing left to the owner.** `server.json` follows schema
    2025-12-11 and is checked by a test, but nothing was published, for three reasons. The
    registry lists PyPI packages and `econ-gateway` depends on `labor-agent`, which is not on PyPI.
    The `io.github.<user>` name must be proven by logging in with the owner's GitHub account. And
    publishing is an outward-facing action on the owner's accounts. `docs/PUBLISHING.md` lists the
    blockers and the exact steps, including a remote-only listing for a hosted HTTP deployment,
    which avoids the PyPI blocker.

## 6. Phase 1 results (2026-11-07)

- **Checks:** 57 unit + MCP tests pass offline (fake upstream). 3 live tests pass. ruff and
  pyright (standard mode) are clean.
- **Live stdio smoke test** (`scripts/smoke_live.py`, same transport as Claude Desktop):
  - all 4 tools work;
  - responses are 0.8–2.1K characters;
  - each search or lookup finishes in 1–2 tool calls.
- **Not done yet:**
  - agent-level evals with a real LLM client (Phase 6);
  - a recorded-fixture test of `LiveUpstream` (it is covered by live tests only);
  - a README tool table generated from the server (Phase 3).

## 7. Phase 2 results (2026-11-07)

- **Tools:** 8 in total (cap 12), in three toolsets: core 5, wages 2, regional 1. Everything
  is mounted by default.
- **Checks:** 87 offline tests and 6 live tests pass; ruff and pyright are clean. The fakes in
  `tests/fake_places.py` reproduce the real edge cases:
  - top-coded wages;
  - annual-only occupations;
  - suppressed QCEW cells;
  - 7-digit non-metro areas;
  - duplicate QCEW codes.
- **PDF demo, live over stdio** (`find_occupation` → `find_area` → `get_occupation_wages`):
  - Registered Nurses in Texas, May 2025: median $95,970, mean $95,380, 271,380 jobs;
  - national median $97,550, returned in the same call.
- **Other live checks:**
  - Austin metro unemployment 3.7% (Sep 2026, NSA, preliminary), unchanged from a year earlier;
  - Travis County QCEW 2026-Q1: 931,218 jobs, $2,087 average weekly wage, +5.4% year on year.
- **Connected to Claude Code** (local scope, this folder): `claude mcp get econ-gateway`
  reports Connected.
- **Not done yet:** a headless end-to-end question through Claude Code. The `claude` CLI's
  sign-in had expired (401).

## 8. Phase 3 results (2026-11-08)

- **Tools:** 10 in total (cap 12): core 5, wages 2, regional 1, compute 2.
- **Checks:** 108 offline tests and 8 live tests pass; ruff and pyright are clean.
- **Docs:** `scripts/gen_tool_docs.py` generates the README table, `docs/TOOLS.md` and the
  schema snapshot. `tests/test_tool_docs.py` fails when any of them is stale.
- **Agreement with Project 1, and a data revision:**
  - Live, `real_wage_growth("CES3000000003")` gave Sep 2026 manufacturing pay of $37.06,
    +3.81% nominal against +3.717% CPI, so real pay is **+0.09%**.
  - Project 1's verified briefing (run 2026-11-06) said $36.92, +3.42%, real **−0.29%**.
  - Cause: BLS revised September pay from $36.92 to $37.06 (and August from $36.90 to $36.93)
    in the October jobs report, published between the two runs.
  - On Project 1's archived inputs the gateway reproduces 3.417 / 3.717 / −0.289 exactly; a
    fixed-data test (`test_real_wage_method_matches_project_1_on_identical_inputs`) pins this.
  - Project 1's README demo answer is now out of date. That is a data change, not a method
    error.
- **Live finding fixed (decision 22):** `compare_series` compared October wage growth with
  September CPI. Aligning everything to the benchmark's month changed the leader from
  Construction to Information: +5.47%, 1.75 points above CPI.

## 9. Phase 4 results (2026-11-08)

- **Tools:** unchanged (10). One output change: `SeriesMeta.stale_as_of`, null unless the data
  is a cached copy served during an outage. The schema snapshot was regenerated.
- **Checks:** 146 offline tests and 8 live tests pass; ruff and pyright are clean.
  - `tests/test_reliability.py`: 30 unit tests of the token bucket, breaker (including the
    half-open probe), single-flight, quota (restart and the Eastern-midnight rollover),
    guarded adapter (allow-list, plain HTTP, timeout caps, 5xx vs 4xx, rate limit, quota),
    release-aware cache and the freshness rule.
  - `tests/test_upstream_resilience.py`: 8 end-to-end tests. They run Project 1's real clients
    over a fake transport and cover cache hits without network, stale fallback through
    `get_latest` and `get_series`, outage without cache, the breaker tripping, merged
    concurrent fetches, a BLS "daily threshold" refusal, and the metadata reserve.
- **Live:**
  - Release IDs resolved correctly. Next releases: CPI 2026-11-10, claims 2026-11-12, metro
    unemployment 2026-12-02, Employment Situation 2026-12-04.
  - `get_latest` on 4 series took 1.29 s cold and 0.07 s from the release-aware cache.
  - The BLS quota file counted 4 queries for the whole smoke run (15 calls), which shows how
    far the 500/day budget goes.
  - All 15 stdio smoke calls pass.
- **Not done yet:**
  - a live outage drill (the fallback is tested only against a fake transport);
  - exposing `LiveUpstream.status()` as a resource or health endpoint (Phases 5 and 7).

## 10. Phase 5 results (2026-11-08)

- **Surface:** 10 tools (unchanged), 4 resources, 3 resource templates and 4 prompts.
  `describe_series` gained `release` (last and next release date). Snapshots regenerated.
- **Checks:** 169 offline tests and 9 live tests pass; ruff and pyright are clean.
  `tests/test_reference.py` (23 tests) covers release schedules, catalog filtering, the series
  resource, status (key values never appear), methodology notes, the MCP listing, error
  messages, prompt filtering by toolset, and the consistency of prompt and note text with the
  code.
- **Live over stdio** (`scripts/smoke_live.py`, now also reading 5 resources and rendering a
  prompt):
  - `econ://releases`: CPI next on 2026-11-10, jobless claims 2026-11-12, PPI 2026-11-13.
  - `econ://status`: breakers closed; 8 of 500 BLS queries used today.
  - `econ://series/UNRATE` and `econ://catalog/claims` return in milliseconds from the cache.
- **Not done yet:**
  - trying the prompts and resources in Claude Desktop's UI (only tested through the MCP client);
  - argument completion for resource templates and prompts (e.g. suggesting categories).

## 11. Phase 6 results (2026-11-08)

- **Harness:** `evals/` holds the reference client (OpenAI-compatible; OpenAI or local Ollama),
  20 cases (12 dev, 8 held-out) with live oracles, a deterministic grader, a runner and JSON/Markdown
  reports. `python -m evals --oracles-only` prints expected answers without LLM calls.
- **Checks:** 191 offline tests pass (22 new in `tests/test_evals.py`: grader formats and
  tolerances, the case set, the agent loop against the in-memory server with a scripted model, tool
  errors, budget-forced answers, runner, report and early abort). ruff and pyright are clean.
- **Live run** (`gpt-4.1-mini`, 2026-11-08, `evals/reports/eval_20261108-200741.md`):

  | Mode | Split | Accuracy | Tool calls / answer | Tool errors | Input tokens / answer | Latency / answer |
  |---|---|---|---|---|---|---|
  | gateway | dev | **12/12** | 2.1 | 0 | 14.9K | 4.2 s |
  | gateway | held-out | **7/8** | 2.4 | 0 | 17.6K | 4.2 s |
  | baseline (no tools) | dev | 1/12 | 0 | 0 | 71 | 1.3 s |
  | baseline (no tools) | held-out | 1/8 | 0 | 0 | 71 | 1.2 s |

  - With the gateway, every question took 1–5 tool calls (the ≤ 3-call goal of §1 held for 18 of
    20), with no tool errors and no forced stops. About 320K input tokens in all: roughly $0.13 at
    gpt-4.1-mini list prices.
  - Without it, the model declined to give current figures (correctly) and passed only the
    abstention case and the jobs-report date, which it inferred from the "first Friday" rule.
  - The county-wage question worked as designed: `get_occupation_wages` for `county:48453` returned
    a "no county data, use the metro" item, and the model followed it to Austin.
- **Held-out failure, kept as a finding (not tuned away):** `unemployment_change_year`. October
  2025 was never published, so the latest year-over-year change does not exist. The model called
  `get_series` with `end_date=2026-09` and reported September's change (−0.2 points) without saying
  that October 2026 is out or that its comparison month is missing. The numbers are right for the
  month it named; the answer is incomplete. Candidate fix for the next round (on dev cases only):
  make `get_latest` the obvious tool for "change over the past year" and have its gap note suggest
  the nearest published comparison.
- **Cost driver:** input tokens are ~90% tool definitions and server instructions (about 4.5K per
  LLM call). Mounting fewer toolsets (`ECON_TOOLSETS`) is the lever if a client needs it cheaper.
- **Bug found by the harness, fixed:** Project 1's `.env` defines an empty `OPENAI_BASE_URL`, which
  the OpenAI SDK read as the base URL; the client now passes an explicit URL. The run also aborts
  after 3 consecutive initial errors instead of finishing 40 failed answers.
- **Gateway fix found while running:** the `prompts` sub-server did not set
  `mask_error_details=True`; it does now.
- **Round 2: fixing the gap failure (decision 41).** Tuned on dev only, then confirmed on
  held-out.
  - Added two dev cases with the same trap (participation rate and employment-population ratio, both
    missing October 2025) and tightened the shared oracle: the answer must also name the latest
    month. Before the fix they failed 6 of 6 attempts (one answer labelled September's value as
    October 2025).
  - After the fix, all 6 dev attempts gave the honest answer in 2 tool calls instead of 3. One was
    graded a failure because it said "has not been published", a phrasing the grader's list lacked.
    That was a grader false negative, so the phrase list was widened; the criterion was not changed.
  - Full re-run (`evals/reports/eval_20261108-203823.md`): **gateway 14/14 dev and 8/8 held-out**,
    0 tool errors, 0 forced stops, 2.1–2.4 calls and ~16–17K input tokens per answer. Baseline
    (no tools) 1/14 and 1/8. The held-out `unemployment_change_year` now passes: "October 2025 is not
    published ... nearest published values are 4.4% in September 2025 and 4.5% in November 2025."
- **Not done yet:** repeated runs for consistency (`--repeat 3`), a run with a local Ollama model,
  and a run with Claude once an Anthropic key is available.

## 12. Phase 7 results (2026-11-08)

- **Added:** Streamable HTTP with JWT auth, `/health` and `/ready`, strict Host/Origin checks, a
  safe-bind guard, OpenTelemetry tracing, a Dockerfile with Dockerfile-specific ignore file, a
  hardened `docker-compose.yml`, and `scripts/smoke_http.py`.
- **Checks:** 224 offline tests and 9 live tests pass; ruff and pyright are clean.
  - `tests/test_http.py` (24 tests) uses the real HTTP app in-process. It covers settings
    validation, CSV parsing, the safe-bind guard, probes (ready, degraded, not ready), tools over
    HTTP, Host checks, JWT auth, and tracing.
  - JWT auth: a valid token works; a missing token, wrong audience, wrong issuer, expired token
    and a token signed by another key are refused. A positive control shows the 401s are not an
    artifact of the test request.
  - Tracing: no URL or key in span attributes, and console spans never reach stdout.
- **Live** (`scripts/smoke_http.py`, the image's entry point as a real process over real HTTP): 9/9.
  - Refuses 0.0.0.0 without auth.
  - `/health` and `/ready` answer without a token (296 catalog series, circuits closed).
  - No token gets 401, a token for another audience gets 401, and a foreign Host gets 421.
  - With a valid token: 10 tools, live `get_latest` (unemployment 4.1%, October 2026) and OEWS wages
    (RN Texas median $95,970).
- **Bug found and fixed:** comma-separated list settings crashed at startup (decision 42).
- **Not done yet:** building and running the image. Docker is not installed on this machine; the
  Dockerfile and compose file are written but unbuilt.

## 13. Phase 8 results (2026-11-08)

- **Demo** (`docs/DEMO.md`, recorded live with `gpt-4.1-mini`), every number checked against
  earlier verified values:
  - the PDF question (RN wages in Texas): $95,970 median, May 2025, in 3 tool calls;
  - real manufacturing wages: +3.81% vs CPI +3.717%, real +0.09%;
  - Travis County: 931,218 jobs, $2,087 weekly wage, 3.6% unemployment;
  - the year-over-year unemployment question answered honestly about the October 2025 gap.
- **Docs:** README rewritten as a front page (from 466 lines to 234) with a Mermaid
  architecture diagram and an eval results table. New `docs/DEPLOYMENT.md`, `EXAMPLES.md`,
  `DEVELOPMENT.md`, `PUBLISHING.md` and `CHANGELOG.md`.
  - Fixed stale content found on the way: the `get_latest` example showed the gap note from
    before decision 41, and the eval counts said 20 questions instead of 22.
  - Every reference to the old README "HTTP deployment" section now points to `docs/DEPLOYMENT.md`.
- **Release:** version 1.0.0 (decision 52); `server.json` prepared (decision 53).
- **Checks:** 229 offline tests pass (5 new in `tests/test_packaging.py`); ruff and pyright are
  clean; the docs check, stdio smoke, HTTP smoke and live tests pass.
- **Observation for the next eval round:** in the Travis County demo the model passed
  `county:48453` to `get_area_employment` before calling `find_area`, building the ID from the
  county's FIPS code. It was right, but it is the kind of invented ID the instructions forbid. It
  then called `find_area` twice with the same arguments. Both are worth an eval case.
- **Owner's next steps:** build the Docker image on a machine with Docker; decide how to make
  Project 1's data layer installable from PyPI (or list a hosted gateway as a remote server); then
  publish with `docs/PUBLISHING.md`.

## 14. Self-contained repository (2026-11-08)

54. **The gateway owns its data layer; Project 1 is no longer a dependency.** This supersedes
    decision 2 ("import Project 1, don't copy it").
    - The user wants the two projects to run independently and to be tested separately (Docker,
      GitHub). They may build on each other but do different jobs (README "How this differs from
      Project 1").
    - The 11 modules the gateway used from `labor_agent.data` and `labor_agent.resolve` (BLS, FRED,
      OEWS and QCEW clients, disk cache, HTTP session, catalog, `get_series`, BM25 retrieval, text
      normalization, entity matching) were copied at Project 1 commit `bdfb586` into
      `econ_gateway.sources` and `econ_gateway.resolve`. The curated catalog and OEWS/QCEW reference
      tables ship as package data in `sources/reference/`.
    - The logic and the cache-key format are unchanged. The code was brought up to this repo's
      standards (docstrings, types, pyright). `DiskCache` gained public `paths()` and
      `fetched_at()`, so the release-aware cache no longer reaches into a private method.
      `LaborAgentUpstream` was renamed `LiveUpstream`.
    - Project 1's LLM resolver and analysis code were not copied: the gateway has no LLM.
    - Settings keep Project 1's variable names (`BLS_API_KEY`, `CACHE_DIR`...), now read from this
      project's `.env`. Caches default to `data/` inside the repository.
    - Project 1's data-layer tests were ported (`tests/test_sources.py`, 32 tests, including
      resolver checks).
    - Proof of independence: `labor-agent` was uninstalled from the gateway's environment.
      - 261 offline and 9 live tests pass, as do the stdio smoke test and the HTTP smoke test (9/9).
      - A clean, non-editable install of only the files the Dockerfile copies loads the catalog,
        the reference tables and the methodology notes, and builds the server.
    - Docker now builds from the repository root (`docker build -t econ-gateway:1.0.0 .`) with a
      standard `.dockerignore`.
    - The registry name is `io.github.Eklavya17/econ-gateway`, and the README carries the `mcp-name`
      marker. With no unpublished dependency left, the PyPI blocker of decision 53 is gone.
