"""Incident Lens web console: the same MCP tools, Claude, and evidence ledger as cli/ask.py and
the MCP server itself - this is a UI layer, not a second implementation. Every number it shows
came from a real tool call against the real SereneDB corpus; nothing here is precomputed or faked.

Run:  python ui/server.py                 (needs ANTHROPIC_API_KEY for the chat; the ledger/overview
                                            panels work without one)
Then open http://127.0.0.1:8787
"""
import asyncio, json, os, pathlib, shutil, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starlette.applications import Starlette
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route
from starlette.requests import Request
from sse_starlette.sse import EventSourceResponse

from server.app import mcp
from server import tools as T, ledger, config

ROLE_PROMPT = {"engineer": "engineer_investigation", "lead": "lead_review", "manager": "manager_priorities"}
MODEL = os.environ.get("INCIDENT_LENS_MODEL", "claude-sonnet-5-5")

# ---------------------------------------------------------------- static pages
async def index(request):
    return FileResponse(ROOT / "ui" / "static" / "index.html")

# ---------------------------------------------------------------- dashboard data
async def overview(request):
    try:
        r = await asyncio.to_thread(T.corpus_overview)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    return JSONResponse(r)

async def ledger_status(request):
    try:
        return JSONResponse(ledger.verify_chain())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)

def _run_tamper_demo():
    """Sync helper run off the event loop (via asyncio.to_thread) - it holds ledger._lock, the
    same lock record() takes, for as long as the snapshot copy needs: a blocking lock held inside
    an async route body would freeze the whole event loop for every other concurrent request
    while contended, not just this one."""
    import sqlite3
    with ledger._lock:   # without this, shutil.copyfile could race a concurrent record() write
        config.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not config.LEDGER_PATH.exists():
            T.corpus_overview()  # seed at least one entry to demo against
        with tempfile.TemporaryDirectory() as td:
            tmp_path = pathlib.Path(td) / "tamper_demo.sqlite"
            shutil.copyfile(config.LEDGER_PATH, tmp_path)
            c = sqlite3.connect(tmp_path)
            row = c.execute("SELECT evidence_id FROM evidence ORDER BY RANDOM() LIMIT 1").fetchone()
            if not row:
                c.close()
                return {"error": "ledger is empty - run a query first"}, 400
            target = row[0]
            c.execute("UPDATE evidence SET total_matches = COALESCE(total_matches, 0) + 1 WHERE evidence_id = ?", (target,))
            c.commit(); c.close()
            tampered_result = ledger.verify_chain(path=tmp_path)
    clean_result = ledger.verify_chain()  # the real ledger, read fresh, outside the lock
    return {"tampered_entry": target, "after_tamper": tampered_result, "real_ledger_unaffected": clean_result}, 200

async def ledger_tamper_demo(request):
    """Prove tamper-detection without touching the real ledger: snapshot it to a temp file,
    corrupt one row in the COPY, and verify the copy there. `ledger.verify_chain(path=...)` reads
    that path directly - the live config.LEDGER_PATH is never reassigned, even briefly, which
    matters because it's a module-level global every other concurrent request also reads."""
    try:
        body, status = await asyncio.to_thread(_run_tamper_demo)
        return JSONResponse(body, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def evidence_detail(request):
    eid = request.path_params["evidence_id"]
    try:
        e = ledger.get(eid)
    except Exception as ex:
        return JSONResponse({"error": str(ex)}, status_code=503)
    if not e:
        return JSONResponse({"error": f"unknown evidence_id {eid!r}"}, status_code=404)
    return JSONResponse(e)

# ---------------------------------------------------------------- the chat loop, streamed
def _sse(event, data):
    return {"event": event, "data": json.dumps(data, default=str)}

def _validate_history(history):
    """history is prior (role, content) turns the CLIENT accumulates and resends - this server is
    otherwise stateless across requests. Validated defensively the same way every other input
    boundary in this project is: a malformed turn must raise a clean, readable ValueError, not
    surface as a cryptic failure three calls deep inside the Anthropic SDK."""
    if history is None: return []
    if not isinstance(history, list): raise ValueError(f"history must be a list, got {type(history).__name__}")
    out = []
    for i, turn in enumerate(history):
        if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant") or not isinstance(turn.get("content"), str):
            raise ValueError(f"history[{i}] must be an object like {{'role': 'user'|'assistant', 'content': <string>}}, got {turn!r}")
        out.append({"role": turn["role"], "content": turn["content"]})
    return out

async def run_conversation(role, dataset, question, client=None, max_steps=12, history=None):
    """The tool-calling loop, as a plain async generator of event dicts (not SSE-framed) so it can
    be driven directly in a test with a fake `client` - everything downstream of the Anthropic call
    (prompt building, mcp.call_tool, the real tools/SQL/ledger) is the real thing either way.
    `client` is injectable; production passes nothing and gets a real anthropic.Anthropic().
    `history`: prior (role, content) turns from the same conversation, so a follow-up question
    ("what about OpenStack?") actually has context - without this every question started a brand
    new, memory-less conversation, which breaks the second a real user (or a judge) asks a follow-up."""
    question = (question or "").strip()
    role = role if role in ROLE_PROMPT else "engineer"
    dataset = dataset if dataset in ("bgl", "openstack") else "bgl"
    if not question:
        yield ("error", {"message": "question must not be empty"}); return
    try:
        history = _validate_history(history)
    except ValueError as e:
        yield ("error", {"message": str(e)}); return
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            yield ("error", {"message": "ANTHROPIC_API_KEY is not set in this server's environment. "
                                         "Export it and restart ui/server.py to enable live chat."})
            return
        import anthropic
        client = anthropic.Anthropic()
    try:
        pr = await mcp.get_prompt(ROLE_PROMPT[role], {"topic": question, "dataset": dataset})
        system = pr.messages[0].content.text
        tool_defs = [{"name": t.name, "description": t.description,
                      "input_schema": getattr(t, "input_schema", None) or t.inputSchema}
                     for t in await mcp.list_tools()]
    except Exception as e:
        yield ("error", {"message": f"could not set up the conversation: {e}"})
        return
    msgs = history + [{"role": "user", "content": question}]
    yield ("start", {"role": role, "dataset": dataset, "question": question})
    try:
        for step in range(max_steps):
            r = await asyncio.to_thread(client.messages.create, model=MODEL, max_tokens=4000,
                                         system=system, tools=tool_defs, messages=msgs)
            msgs.append({"role": "assistant", "content": r.content})
            calls = [b for b in r.content if b.type == "tool_use"]
            thinking_text = "".join(b.text for b in r.content if b.type == "text").strip()
            if thinking_text:
                yield ("thinking", {"text": thinking_text})
            if not calls:
                yield ("answer", {"text": thinking_text})
                break
            results = []
            for c in calls:
                yield ("tool_call", {"tool": c.name, "args": c.input})
                t0 = time.time()
                res = await mcp.call_tool(c.name, c.input)
                ms = round((time.time() - t0) * 1000, 1)
                envelope = json.loads(res.content[0].text)
                yield ("tool_result", {
                    "tool": c.name, "evidence_id": envelope.get("evidence_id"),
                    "total_matches": envelope.get("total_matches"), "returned": envelope.get("returned"),
                    "truncated": envelope.get("truncated"), "sql": envelope.get("sql"),
                    "ledger_hash": envelope.get("ledger_hash"), "ms": ms,
                    "scope": envelope.get("scope"),
                    # generate_report's actual payload the UI needs to render it - every other
                    # tool's envelope fields above are enough, this one specifically isn't
                    "markdown": envelope.get("markdown"), "title": c.input.get("title") if c.name == "generate_report" else None,
                })
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": res.content[0].text})
            msgs.append({"role": "user", "content": results})
        else:
            yield ("answer", {"text": "(step limit reached)"})
    except Exception as e:
        yield ("error", {"message": str(e)})
        return
    yield ("done", {})

async def ask_stream(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = (body.get("question") or "").strip()
    role = body.get("role") if body.get("role") in ROLE_PROMPT else "engineer"
    dataset = body.get("dataset") if body.get("dataset") in ("bgl", "openstack") else "bgl"
    history = body.get("history")

    async def gen():
        async for event, data in run_conversation(role, dataset, question, history=history):
            yield _sse(event, data)

    return EventSourceResponse(gen())

STATIC_DIR = (ROOT / "ui" / "static").resolve()

async def static_file(request):
    requested = (STATIC_DIR / request.path_params["path"]).resolve()
    if STATIC_DIR not in requested.parents and requested != STATIC_DIR:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not requested.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(requested)

async def unhandled_error(request, exc):
    """Last line of defense: every route above already catches what it expects to fail, but this
    keeps any future route (or a bug in one of these) from ever handing the browser a raw HTML
    stack trace instead of JSON."""
    return JSONResponse({"error": "internal error", "detail": str(exc)}, status_code=500)

app = Starlette(routes=[
    Route("/", index),
    Route("/api/overview", overview),
    Route("/api/ledger/status", ledger_status),
    Route("/api/ledger/tamper-demo", ledger_tamper_demo, methods=["POST"]),
    Route("/api/evidence/{evidence_id}", evidence_detail),
    Route("/api/ask", ask_stream, methods=["POST"]),
    Route("/static/{path:path}", static_file),
], exception_handlers={Exception: unhandled_error})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("INCIDENT_LENS_UI_PORT", 8787))
    print(f"Incident Lens console: http://127.0.0.1:{port}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("NOTE: ANTHROPIC_API_KEY is not set - the ledger/overview panels will work, chat will not.")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
