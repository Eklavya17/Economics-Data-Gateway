"""Economics Data Gateway: an MCP server giving AI assistants live, cited BLS and FRED data.

Importing this package loads the project's ``.env`` into the environment (without overriding
variables that are already set), so every module, the data-source clients included, sees the
same keys and settings.
"""

from importlib.metadata import PackageNotFoundError, version

from econ_gateway.config import load_env_files

try:
    __version__ = version("econ-gateway")  # single source of truth: pyproject.toml
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0+unknown"

load_env_files()
