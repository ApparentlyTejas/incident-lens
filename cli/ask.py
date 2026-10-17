#!/usr/bin/env python3
"""Ask Claude a question for one role; Claude calls the SAME tools the MCP server exposes.
   export ANTHROPIC_API_KEY=...
   python cli/ask.py --role engineer "Find 'parity error' in BGL; what happened around the first one?"
   python cli/ask.py --role lead --dry-run "..."     # print tools + role prompt, no API call
Fallback for the demo and the engine behind eval/run.py. After the answer it runs eval/verify_answer.py logic.
NOTE: the Anthropic API call path was not exercised in the authoring sandbox (no API key); --dry-run and the tools were."""
import argparse, asyncio, json, os, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "eval"))
from server.app import mcp
from verify_answer import verify

ROLE_PROMPT = {"engineer": "engineer_investigation", "lead": "lead_review", "manager": "manager_priorities"}
MODEL = os.environ.get("INCIDENT_LENS_MODEL", "claude-sonnet-5-5")

async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("question"); ap.add_argument("--role", choices=ROLE_PROMPT, required=True)
    ap.add_argument("--dataset", default="bgl"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--max-steps", type=int, default=12)
    a = ap.parse_args()
    question = (a.question or "").strip()
    if not question:
        sys.exit("question must not be empty")
    pr = await mcp.get_prompt(ROLE_PROMPT[a.role], {"topic": question, "dataset": a.dataset})
    system = pr.messages[0].content.text
    tools = [{"name": t.name, "description": t.description, "input_schema": getattr(t, "input_schema", None) or t.inputSchema} for t in await mcp.list_tools()]
    if a.dry_run:
        print(system[:600], "...\n\ntools:", [t["name"] for t in tools]); return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set - export it, or use --dry-run to see the prompt/tools without calling the API.")
    import anthropic
    client = anthropic.Anthropic(); msgs = [{"role": "user", "content": question}]; trace = []
    try:
        for _ in range(max(1, a.max_steps)):
            r = client.messages.create(model=MODEL, max_tokens=4000, system=system, tools=tools, messages=msgs)
            msgs.append({"role": "assistant", "content": r.content})
            calls = [b for b in r.content if b.type == "tool_use"]
            if not calls:
                text = "".join(b.text for b in r.content if b.type == "text"); break
            results = []
            for c in calls:
                res = await mcp.call_tool(c.name, c.input); body = res.content[0].text
                trace.append((c.name, c.input, json.loads(body).get("evidence_id")))
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": body})
            msgs.append({"role": "user", "content": results})
        else:
            text = "(step limit reached)"
    except anthropic.APIError as e:
        sys.exit(f"Anthropic API error: {e}")
    except Exception as e:
        # a tool call failing mid-investigation (e.g. the model passed bad args) shouldn't crash
        # the whole CLI session with a raw traceback - show the trace made so far and the error
        print("TOOL CALLS:"); [print(f"  {n}({json.dumps(i)}) -> {e}") for n, i, e in trace]
        sys.exit(f"tool call failed: {e}")
    print("TOOL CALLS:"); [print(f"  {n}({json.dumps(i)}) -> {e}") for n, i, e in trace]
    print("\nANSWER:\n" + text)
    checked, problems = verify(text); print(f"\nVERIFY: {checked} cited numbers checked, {len(problems)} problem(s)")
    for p in problems: print("  ", p)

asyncio.run(main())
