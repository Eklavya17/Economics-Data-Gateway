"""Release metadata stays consistent: versions, MCP Registry server.json, documented variables.

The registry's own validation runs only at publish time (``mcp-publisher validate``), so these
checks catch drift locally. Schema constraints are from server.schema.json 2025-12-11
(docs/PUBLISHING.md).
"""

from __future__ import annotations

import json
import re
import tomllib

from econ_gateway import __version__
from econ_gateway.config import PROJECT_ROOT

SERVER_JSON = json.loads((PROJECT_ROOT / "server.json").read_text(encoding="utf-8"))
PYPROJECT = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_versions_agree():
    version = PYPROJECT["project"]["version"]
    assert __version__ == version
    assert SERVER_JSON["version"] == version
    assert all(package["version"] == version for package in SERVER_JSON["packages"])


def test_server_json_meets_registry_constraints():
    assert SERVER_JSON["$schema"].endswith("/2025-12-11/server.schema.json")
    name = SERVER_JSON["name"]
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", name) and 3 <= len(name) <= 200
    assert name.startswith("io.github.")
    assert len(SERVER_JSON["description"]) <= 100
    (package,) = SERVER_JSON["packages"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == PYPROJECT["project"]["name"]
    assert package["transport"] == {"type": "stdio"}


def test_registry_variables_are_documented_and_secret_where_needed():
    documented = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for variable in SERVER_JSON["packages"][0]["environmentVariables"]:
        assert variable["name"] in documented
        assert variable["isSecret"] == variable["name"].endswith("_KEY")
        assert variable["description"]


def test_changelog_has_the_current_version():
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {PYPROJECT['project']['version']} " in changelog


def test_relative_links_in_docs_resolve():
    """Every relative Markdown link in the project docs points at a file that exists."""
    docs = [PROJECT_ROOT / name for name in ("README.md", "DESIGN.md", "CHANGELOG.md", "CLAUDE.md")]
    docs += sorted((PROJECT_ROOT / "docs").glob("*.md"))
    broken = []
    for doc in docs:
        for target in re.findall(r"\]\(([^)\s]+)\)", doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = target.split("#", 1)[0].replace("%20", " ")
            if path and not (doc.parent / path).exists():
                broken.append(f"{doc.name}: {target}")
    assert not broken, broken
