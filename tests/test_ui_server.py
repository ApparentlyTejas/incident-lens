"""Exercises ui/server.py's run_conversation() - the Claude tool-calling loop behind the web
console - end to end EXCEPT for the actual network call to Anthropic, which is faked with an
object shaped exactly like the real SDK's response (content blocks with .type/.text/.name/.input/.id).
Everything else is real: the real role prompts, the real tool list, the real mcp.call_tool ->
real SQL -> real SereneDB -> real evidence ledger. This is what proves the loop logic is correct
without needing an ANTHROPIC_API_KEY (see README's 'Status and known limitations')."""
import types, pytest, pathlib, importlib.util

# ui/server.py is a plain module (not a package) named the same as the top-level `server` package,
# so it's loaded by file path under its own name rather than via sys.path + `import server`, which
# would shadow (or be shadowed by) the real `server` package that ui/server.py itself depends on.
_spec = importlib.util.spec_from_file_location("incident_lens_ui_server", pathlib.Path(__file__).resolve().parent.parent / "ui" / "server.py")
_ui_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ui_server)
run_conversation = _ui_server.run_conversation

def block(type, **kw):
    return types.SimpleNamespace(type=type, **kw)

class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
    def create(self, **kwargs):
        # snapshot kwargs["messages"] - it's the SAME list object run_conversation keeps mutating
        # (appending later steps' turns to), so capturing a reference instead of a copy would make
        # calls[i]["messages"] reflect the conversation's FINAL state, not what was sent at call i.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.script:
            raise AssertionError("fake Anthropic client ran out of scripted responses")
        resp = self.script.pop(0)
        return types.SimpleNamespace(content=resp)

class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)

async def drain(role, dataset, question, script, max_steps=12, history=None):
    client = FakeClient(script)
    events = []
    async for ev, data in run_conversation(role, dataset, question, client=client, max_steps=max_steps, history=history):
        events.append((ev, data))
    return events, client.messages.calls

@pytest.mark.asyncio
async def test_empty_question_short_circuits_without_calling_the_model():
    events, calls = await drain("engineer", "bgl", "   ", script=[])
    assert events == [("error", {"message": "question must not be empty"})]
    assert calls == []

@pytest.mark.asyncio
async def test_direct_answer_no_tool_calls():
    script = [[block("text", text="No tools needed, here's the answer.")]]
    events, calls = await drain("engineer", "bgl", "what is bgl?", script)
    kinds = [e for e, _ in events]
    assert kinds[0] == "start"
    assert ("answer", {"text": "No tools needed, here's the answer."}) in events
    assert kinds[-1] == "done"
    assert len(calls) == 1
    # the real system prompt and real tool schemas were actually built and passed through
    assert "ENGINEER" in calls[0]["system"]
    assert any(t["name"] == "search_logs" for t in calls[0]["tools"])

@pytest.mark.asyncio
async def test_follow_up_question_actually_sees_prior_conversation_history():
    # without this, every question started a brand new memory-less conversation - a real user's
    # (or a judge's) natural follow-up like "what about OpenStack?" would have zero context
    history = [{"role": "user", "content": "first question"},
               {"role": "assistant", "content": "first answer, cites [E-0001]"}]
    script = [[block("text", text="follow-up answer")]]
    events, calls = await drain("engineer", "bgl", "and what about that one specifically?", script, history=history)
    assert ("answer", {"text": "follow-up answer"}) in events
    sent = calls[0]["messages"]
    assert len(sent) == 3   # the 2 prior turns + the new question
    assert sent[0] == history[0] and sent[1] == history[1]
    assert sent[2] == {"role": "user", "content": "and what about that one specifically?"}

@pytest.mark.asyncio
async def test_malformed_history_errors_cleanly_instead_of_crashing():
    for bad in ("not a list", [{"role": "system", "content": "x"}], [{"role": "user", "content": 123}], [{"no": "role"}]):
        events, calls = await drain("engineer", "bgl", "q", script=[], history=bad)
        assert events[0][0] == "error" and "history" in events[0][1]["message"]
        assert calls == []

@pytest.mark.asyncio
async def test_multi_step_tool_call_hits_the_real_tool_and_real_ledger():
    script = [
        [block("text", text="Let me check the level breakdown."),
         block("tool_use", name="count_by", input={"dataset": "bgl", "group_by": "level"}, id="t1")],
        [block("text", text="Done.")],
    ]
    events, calls = await drain("lead", "bgl", "counts by level please", script)
    kinds = [e for e, _ in events]
    assert kinds == ["start", "thinking", "tool_call", "tool_result", "thinking", "answer", "done"]
    tool_call = next(d for e, d in events if e == "tool_call")
    assert tool_call == {"tool": "count_by", "args": {"dataset": "bgl", "group_by": "level"}}
    result = next(d for e, d in events if e == "tool_result")
    # this is a REAL evidence id from a REAL query against the REAL corpus, not a stub
    assert result["evidence_id"].startswith("E-")
    assert result["total_matches"] and result["total_matches"] > 0
    assert result["sql"] and "bgl_logs" in result["sql"][0]
    # the second model call actually received the real tool_result content back
    second_call_msgs = calls[1]["messages"]
    assert second_call_msgs[-1]["role"] == "user"
    assert second_call_msgs[-1]["content"][0]["tool_use_id"] == "t1"

@pytest.mark.asyncio
async def test_generate_report_tool_call_flows_through_the_chat_loop():
    pre = []
    async for ev, data in run_conversation("engineer", "bgl", "__seed__", client=FakeClient(
            [[block("tool_use", name="count_by", input={"dataset": "bgl", "group_by": "level"}, id="s1")],
             [block("text", text="noop")]])):
        pre.append((ev, data))
    seeded_eid = next(d["evidence_id"] for e, d in pre if e == "tool_result")

    script = [[block("tool_use", name="generate_report",
                      input={"title": "t", "body": f"see [{seeded_eid}]", "evidence_ids": [seeded_eid]}, id="t1")],
              [block("text", text="Here's the report.")]]
    events, calls = await drain("engineer", "bgl", "write this up", script)
    kinds = [e for e, _ in events]
    assert "error" not in kinds
    result = next(d for e, d in events if e == "tool_result")
    assert result["tool"] == "generate_report"
    assert result["evidence_id"].startswith("E-")

@pytest.mark.asyncio
async def test_a_tool_error_surfaces_as_a_readable_error_event_not_a_crash():
    script = [[block("tool_use", name="count_by", input={"dataset": "bgl", "group_by": "http_status"}, id="t1")]]
    events, calls = await drain("lead", "bgl", "group by nonsense", script)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "error"
    msg = events[-1][1]["message"]
    assert "group_by must be one of" in msg
    assert "done" not in kinds   # the loop stops at the error, it does not limp forward

@pytest.mark.asyncio
async def test_step_limit_is_enforced_when_the_model_never_stops_calling_tools():
    looping_step = [block("tool_use", name="corpus_overview", input={}, id="tN")]
    events, calls = await drain("manager", "bgl", "keep going forever", script=[looping_step] * 3, max_steps=3)
    assert len(calls) == 3
    assert events[-2] == ("answer", {"text": "(step limit reached)"})
    assert events[-1][0] == "done"
