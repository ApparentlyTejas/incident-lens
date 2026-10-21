#!/usr/bin/env python3
"""Generate docs/TOOLS.md from the live MCP tool definitions so the README never drifts from the code."""
import asyncio, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
from server.app import mcp
async def main():
    out = ["# Incident Lens tools (generated from the MCP server; do not edit)\n"]
    for t in await mcp.list_tools():
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", {})
        out.append(f"## `{t.name}`\n\n{t.description}\n\n**Inputs**\n")
        req = set(schema.get("required", []))
        for k, v in schema.get("properties", {}).items():
            typ = v.get("enum") and "one of " + "/".join(map(str, v["enum"])) or v.get("type") or "any"
            out.append(f"- `{k}`{' (required)' if k in req else ''}: {typ}. {v.get('description','')}")
        out.append("")
    (ROOT / "docs").mkdir(exist_ok=True); (ROOT / "docs" / "TOOLS.md").write_text("\n".join(out))
    print("wrote docs/TOOLS.md")
asyncio.run(main())
