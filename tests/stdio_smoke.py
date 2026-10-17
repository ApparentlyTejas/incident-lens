"""Spawns the server exactly as Claude Desktop / Claude Code would (stdio) and calls a tool."""
import asyncio, os, sys, json, pathlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The MCP SDK does not inherit the parent shell's environment when spawning a server
# (same as Claude Desktop/Code); a real client config sets env vars explicitly, so we do too.
ENV = {k: os.environ[k] for k in ("SERENEDB_DSN", "INCIDENT_LENS_LEDGER", "INCIDENT_LENS_TIMEOUT") if k in os.environ}

async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "server.app"], cwd=str(ROOT), env=ENV)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools(); print("tools:", [t.name for t in tools.tools])
            res = await s.call_tool("search_logs", {"dataset": "bgl", "query": "parity error", "mode": "phrase", "limit": 2})
            body = json.loads(res.content[0].text)
            print("evidence_id:", body["evidence_id"], "| total_matches:", body["total_matches"], "| returned:", body["returned"], "| truncated:", body["truncated"])
            p = await s.get_prompt("engineer_investigation", {"topic": "parity errors"}); print("prompt ok:", len(p.messages[0].content.text), "chars")
            print("STDIO OK")
asyncio.run(main())
