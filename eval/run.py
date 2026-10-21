#!/usr/bin/env python3
"""Drive the 15 golden questions through the same tool-calling loop as cli/ask.py, check the
tool-selection and evidence-citation metrics, and write eval/results.md.

  export ANTHROPIC_API_KEY=...
  python eval/run.py                  # full run against the Anthropic API
  python eval/run.py --dry-run        # no API calls: just resolves params.yaml into each
                                       # question and prints the expected first tool (CI-safe)
"""
import argparse, asyncio, json, os, sys, pathlib, datetime as dt
import yaml
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "eval"))
from server.app import mcp
from verify_answer import verify

ROLE_PROMPT = {"engineer": "engineer_investigation", "lead": "lead_review", "manager": "manager_priorities"}
MODEL = os.environ.get("INCIDENT_LENS_MODEL", "claude-sonnet-5-5")

def resolve(text, params):
    for k, v in params.items():
        text = text.replace(f"<{k}>", str(v))
    return text

async def ask(question, role, dataset, max_steps=12):
    """Same loop as cli/ask.py, inlined so eval/run.py has no import-order dependency on it.
    Raises on a tool/API error rather than swallowing it - the caller in main() is the one
    that must decide whether one bad question should lose the whole golden-set run's results."""
    question = (question or "").strip()
    if not question: raise ValueError("question must not be empty")
    pr = await mcp.get_prompt(ROLE_PROMPT[role], {"topic": question, "dataset": dataset})
    system = pr.messages[0].content.text
    tools = [{"name": t.name, "description": t.description, "input_schema": getattr(t, "input_schema", None) or t.inputSchema}
             for t in (await mcp.list_tools())]
    import anthropic
    client = anthropic.Anthropic(); msgs = [{"role": "user", "content": question}]; trace = []
    text = "(step limit reached)"
    for _ in range(max_steps):
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
    return text, trace

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="resolve params and show expected first tool only, no API calls")
    ap.add_argument("--ids", help="comma-separated golden ids to run, e.g. E1,L3 (default: all)")
    a = ap.parse_args()

    golden = yaml.safe_load((ROOT / "eval/golden.yaml").read_text())
    params = yaml.safe_load((ROOT / "eval/params.yaml").read_text())
    if a.ids: golden = [q for q in golden if q["id"] in a.ids.split(",")]
    if not a.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set - export it, or use --dry-run to resolve params without calling the API.")

    rows, sections = [], []
    first_tool_hits = total_with_expected = 0
    for q in golden:
        question = resolve(q["question"], params)
        if a.dry_run:
            rows.append((q["id"], q["role"], q["first_tool"] or "-", "(dry-run)", "(dry-run)", "-"))
            sections.append(f"## {q['id']} ({q['role']})\n\n**Question:** {question}\n\n**Expected first tool:** {q['first_tool'] or 'n/a (refusal case)'}\n")
            continue
        # one question failing (a tool error, a transient API error) must not lose every other
        # question's already-collected results - eval/results.md is only written once, at the end
        try:
            text, trace = await ask(question, q["role"], "bgl")
        except Exception as e:
            rows.append((q["id"], q["role"], q["first_tool"] or "-", "ERROR", str(e)[:80], "-"))
            sections.append(f"## {q['id']} ({q['role']})\n\n**Question:** {question}\n\n**FAILED:** {e}\n")
            print(f"  {q['id']}: FAILED - {e}")
            continue
        actual_first = trace[0][0] if trace else None
        if q["first_tool"]:
            total_with_expected += 1
            if actual_first == q["first_tool"]: first_tool_hits += 1
        checked, problems = verify(text)
        tool_ok = "y" if (not q["first_tool"] or actual_first == q["first_tool"]) else f"NO (got {actual_first})"
        rows.append((q["id"], q["role"], q["first_tool"] or "-", tool_ok, f"{checked} checked / {len(problems)} problem(s)", len(trace)))
        sections.append(
            f"## {q['id']} ({q['role']})\n\n**Question:** {question}\n\n"
            f"**Checks to verify manually:** {', '.join(q['checks'])}\n\n"
            f"**Tool calls:** {' -> '.join(f'{n}[{e}]' for n, _, e in trace) or '(none)'}\n\n"
            f"**First tool:** expected `{q['first_tool'] or 'n/a'}`, got `{actual_first or 'n/a'}`\n\n"
            f"**Answer:**\n\n{text}\n\n**verify_answer.py:** {checked} cited numbers checked, {len(problems)} problem(s)"
            + ("".join(f"\n- [{k}] {d} <- {s}" for k, s, d in problems) if problems else "")
        )

    lines = [
        "# Golden set results", "",
        f"Generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} with model `{MODEL}`."
        + (" **DRY RUN - no API calls were made.**" if a.dry_run else ""),
        "",
        "| id | role | expected first tool | tool choice ok | verify_answer | tool calls |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows: lines.append("| " + " | ".join(str(x) for x in r) + " |")
    if not a.dry_run and total_with_expected:
        lines.append("")
        lines.append(f"**Tool-selection rate:** {first_tool_hits}/{total_with_expected} "
                      f"({100.0*first_tool_hits/total_with_expected:.0f}%) chose the intended first tool from its description alone.")
    lines.append("")
    lines += sections
    (ROOT / "eval/results.md").write_text("\n".join(lines))
    print(f"wrote eval/results.md ({len(rows)} questions{', dry run' if a.dry_run else ''})")

if __name__ == "__main__":
    asyncio.run(main())
