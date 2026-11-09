"""The README table, docs/TOOLS.md and the schema snapshot must match the code (decision 21)."""

from econ_gateway.devtools import tool_docs


async def test_generated_docs_and_snapshot_are_current():
    stale = await tool_docs.check()
    assert not stale, (
        "Tool interface changed without regenerating docs. Run "
        "`.venv/Scripts/python.exe scripts/gen_tool_docs.py` and review the diff of: "
        + ", ".join(p.name for p in stale)
    )


def test_type_names_are_readable():
    assert tool_docs.type_name({"anyOf": [{"type": "string"}, {"type": "null"}]}) == "str \\| null"
    assert tool_docs.type_name({"type": "array", "items": {"type": "integer"}}) == "list[int]"
    assert tool_docs.type_name({"enum": ["a", "b"]}) == "`a` \\| `b`"
