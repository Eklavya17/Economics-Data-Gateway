# Changelog

Release notes for the Economics Data Gateway. Design reasons for each change are in
[DESIGN.md](DESIGN.md), referenced by decision number.

## 1.0.0 (2026-11-08)

First complete release: everything planned in DESIGN.md §4, Phases 0–8.

**Self-contained:** the gateway carries its own data layer (`econ_gateway.sources`,
`econ_gateway.resolve`, curated catalog and reference tables) and no longer needs Project 1
installed alongside it (decision 54).

**Tools (10)**
- Series: `search_series`, `describe_series`, `get_latest`, `get_series`.
- Places: `find_area`.
- Wages: `find_occupation`, `get_occupation_wages`.
- Regional: `get_area_employment`.
- Compute: `real_wage_growth`, `compare_series`.
- They are grouped into toolsets selected with `ECON_TOOLSETS`. Each has a typed output schema
  and responses capped in size.

**Resources and prompts**
- 4 resources and 3 resource templates under `econ://`: catalog, series, release calendar,
  methodology notes and status.
- 4 prompts. Each is registered only when the toolsets it uses are mounted (decisions 29–34).

**Data quality**
- Rates change in percentage points, and "year ago" means the same calendar period.
- Real wages use the exact formula, and comparisons are made at the same months (decisions 18–22).
- Unpublished periods are named together with their nearest published neighbours, and newer data
  cut off by `end_date` is flagged (decision 41).
- Top-coded OEWS pay and suppressed QCEW cells are never shown as numbers.

**Reliability (decisions 23–28)**
- The cache follows the agencies' release calendar.
- Outages fall back to the last cached copy, labelled with when it was fetched.
- Every request goes through rate limits and a circuit breaker; the BLS daily quota is tracked
  and identical requests are merged.
- Upstream hosts are allow-listed, HTTPS only.

**Deployment (decisions 42–49)**
- Streamable HTTP with JWT auth (issuer and audience required), strict Host/Origin checks, and
  a refusal to expose an unauthenticated endpoint.
- `/health` and `/ready` probes.
- OpenTelemetry tracing.
- Dockerfile and a hardened `docker-compose.yml`.

**Quality**
- 220+ offline tests: services on fakes, MCP contract tests, HTTP and auth, reliability, evals.
- Live tests against BLS and FRED, plus stdio and HTTP smoke scripts.
- Generated tool docs and interface snapshots, checked by a test.
- Evals: 22 questions graded against live oracles. The gateway got 22/22 with `gpt-4.1-mini`;
  the same model without it got 2/22.
- A recorded demo ([docs/DEMO.md](docs/DEMO.md)).

**Fixed during development**
- Comma-separated list settings crashed at startup (decision 42).
- `compare_series` compared October wages with September CPI (decision 22).
- The `prompts` sub-server did not mask error details (DESIGN.md §11).

**Not yet done**
- The Docker image has not been built on the development machine (no Docker there).
- Not on PyPI or in the MCP Registry yet; see [docs/PUBLISHING.md](docs/PUBLISHING.md).
