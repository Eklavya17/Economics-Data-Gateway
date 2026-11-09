# Publishing to the MCP Registry

The official [MCP Registry](https://registry.modelcontextprotocol.io) lists servers so that
clients can find and install them. Its metadata for this server is ready in
[`server.json`](../server.json) as `io.github.Eklavya17/econ-gateway`, but **the gateway is not
published yet**: publishing needs the owner's PyPI and GitHub logins.

Source: the registry's publishing quickstart
(github.com/modelcontextprotocol/registry, `docs/modelcontextprotocol-io/quickstart.mdx`) and
schema `2025-12-11`, read on 2026-11-08. The registry was in preview then, with its API frozen at
v0.1; re-check the quickstart before publishing.

## Blockers

The earlier blocker, a dependency on Project 1's unpublished `labor-agent` package, is gone: the
gateway now carries its own data layer (DESIGN.md decision 54) and depends only on PyPI packages.
The code is on GitHub at `Eklavya17/Economics-Data-Gateway`.

Ownership checks are already prepared:
- `io.github.Eklavya17/...` names are verified by logging in as that GitHub user.
- The registry checks the PyPI package README for `mcp-name: io.github.Eklavya17/econ-gateway`.
  `README.md` carries it as an HTML comment on its first lines, so it does not render.

## Steps

1. Make the versions agree: `pyproject.toml` `version`, and both `version` fields in
   `server.json`. A test fails when they differ.
2. Build and upload the package (needs a PyPI account and API token):

   ```bash
   uv build
   ```

   ```bash
   uv publish
   ```

3. Install the publisher CLI from the registry's releases page, then sign in with GitHub. The
   device flow asks you to open github.com/login/device and enter a code:

   ```bash
   mcp-publisher login github
   ```

4. Validate and publish:

   ```bash
   mcp-publisher validate
   ```

   ```bash
   mcp-publisher publish
   ```

5. Check the listing:

   ```bash
   curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=econ-gateway"
   ```

## Listing a hosted gateway instead

If the gateway runs as a public HTTP service (see [DEPLOYMENT.md](DEPLOYMENT.md)), it can be
listed as a remote server. That avoids the PyPI blocker. Add the following next to (or instead
of) `packages`:

```json
"remotes": [
  { "type": "streamable-http", "url": "https://econ.example.com/mcp" }
]
```

The URL must be publicly reachable, and clients need a token from your identity provider.

## Constraints the schema enforces

- `name`: `^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`, 3–200 characters.
- `description`: at most 100 characters.
- `version`: an exact version (no ranges, not `latest`).
- `environmentVariables[].name` is required; `isRequired` and `isSecret` default to false.

`tests/test_packaging.py` checks these locally.
