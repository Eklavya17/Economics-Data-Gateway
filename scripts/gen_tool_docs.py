"""Regenerate the README tool table, docs/TOOLS.md and the tool schema snapshot.

    .venv/Scripts/python.exe scripts/gen_tool_docs.py           # write
    .venv/Scripts/python.exe scripts/gen_tool_docs.py --check   # exit 1 if anything is stale

Run it after changing any tool's name, parameters, docstring or response model, and review
the snapshot diff: it is the tool interface every client depends on.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from econ_gateway.devtools import tool_docs


def main() -> int:
    """Write or check the generated files; returns the process exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report stale files, write nothing")
    args = parser.parse_args()
    if args.check:
        stale = asyncio.run(tool_docs.check())
        for path in stale:
            print(f"stale: {path}")
        return 1 if stale else 0
    for path in asyncio.run(tool_docs.write()):
        print(f"updated: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
