"""Run against the sample load:  python scripts/load.py --sample && pytest -q
Ground truth is computed with plain SQL on the base tables (no index, no tool code)."""
import json, sys, pathlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from server import tools as T, db, ledger, config
from server.app import mcp

def gt(sql, params=None): return db.scalar(sql, params)

def test_corpus_overview_matches_plain_sql():
    r = T.corpus_overview()
    assert r["datasets"]["bgl"]["rows"] == gt("select count(*) from bgl_logs")
    assert r["datasets"]["openstack"]["rows"] == gt("select count(*) from openstack_logs")
    assert sum(x["entries"] for x in r["datasets"]["bgl"]["by_team"]) == r["datasets"]["bgl"]["rows"]   # every component has an owner

def test_search_total_matches_plain_sql_and_truncation():
    r = T.search_logs("bgl", "parity error", mode="phrase", limit=3)
    # the index is word-tokenized with no stemming, so "parity error" correctly excludes "parity errors";
    # a plain LIKE is a substring match and would over-count the plural - exclude it explicitly for a fair ground truth.
    assert r["total_matches"] == gt("select count(*) from bgl_logs where message like '%parity error%' and message not like '%parity errors%'")
    assert r["returned"] == 3 and r["truncated"] is True and "never" in r["guidance"]
    assert r["summary_over_all_matches"]["first_seen"] <= r["summary_over_all_matches"]["last_seen"]

def test_search_modes_agree_on_typo():
    exact = T.search_logs("bgl", "parity", mode="terms", limit=1)["total_matches"]
    fuzzy = T.search_logs("bgl", "paritty", mode="fuzzy", limit=1)["total_matches"]
    assert fuzzy == exact > 0

def test_search_logs_rejects_a_null_byte_instead_of_crashing_with_a_raw_db_error():
    # a NUL byte reaches psycopg as a raw DataError that the MCP layer reports with zero detail
    # (deliberately - only our own ValueErrors get surfaced with a message) - so this has to be
    # caught before the query, not after, or the model gets an opaque, non-actionable failure.
    with pytest.raises(ValueError, match="NUL"):
        T.search_logs("bgl", "parity\x00error")
    with pytest.raises(ValueError, match="NUL"):
        T.count_by("bgl", "level", query="parity\x00error")

def test_search_logs_drops_redundant_ts_bound_but_keeps_narrow_one_correct():
    # whole-corpus (no from_ts/to_ts) search: the ts bound would be a no-op, and SereneDB's
    # inverted-index engine loses its fast top-k scoring path whenever ANY range filter is
    # ANDed in alongside ORDER BY BM25 - so it must be dropped from the executed SQL entirely.
    full = T.search_logs("bgl", "parity error", mode="phrase", limit=1)
    assert all("ts >=" not in s for s in full["sql"])
    assert full["total_matches"] == gt("select count(*) from bgl_logs where message like '%parity error%' and message not like '%parity errors%'")
    # an explicit, genuinely narrower range must still filter correctly (and keep the bound in the SQL)
    narrow = T.search_logs("bgl", "parity error", mode="phrase", limit=1, from_ts="2005-06-01", to_ts="2005-07-01")
    assert any("ts >=" in s for s in narrow["sql"])
    assert narrow["total_matches"] == gt("select count(*) from bgl_logs where message like '%parity error%' and message not like '%parity errors%' and ts >= '2005-06-01' and ts < '2005-07-01'")

def test_time_filter_is_exclusive_upper_bound():
    r = T.search_logs("bgl", "parity error", mode="phrase", from_ts="2005-06-03", to_ts="2005-06-04")
    assert r["total_matches"] == gt("select count(*) from bgl_logs where message like '%parity error%' and message not like '%parity errors%' and ts >= '2005-06-03' and ts < '2005-06-04'")

def test_count_by_team_matches_sql_and_sums_to_total():
    r = T.count_by("bgl", "team", severity="severe")
    sql = gt("select count(*) from bgl_logs where level in ('FATAL','FAILURE','SEVERE','ERROR')")
    assert r["total_matches"] == sql == sum(x["entries"] for x in r["rows"])

def test_count_by_rejects_bad_group():
    with pytest.raises(ValueError): T.count_by("bgl", "http_status")

def test_trend_buckets_sum_to_total():
    r = T.trend("bgl", "week", severity="severe")
    assert sum(b["entries"] for b in r["rows"]) == r["total_matches"]
    assert r["rows"][0]["partial_bucket"] is True

def test_episodes_never_exceed_entries_and_state_gap_rule():
    r = T.episodes("bgl", severity="severe", gap_seconds=300)
    assert 0 < r["episodes"] <= r["entries"] and "300s" in r["gap_rule"]
    assert r["entries"] == gt("select count(*) from bgl_logs where level in ('FATAL','FAILURE','SEVERE','ERROR')")
    wide = T.episodes("bgl", severity="severe", gap_seconds=86400)
    assert wide["episodes"] <= r["episodes"]          # bigger gap -> fewer episodes

def test_recurring_patterns_matches_sql_and_new_in_period_is_null_without_a_bound():
    r = T.recurring_patterns("bgl", limit=50)
    assert r["total_matches"] == gt("select count(*) from bgl_logs")
    assert r["templates_total"] == gt("select count(distinct template) from bgl_logs")
    assert sum(x["entries"] for x in r["rows"]) <= r["total_matches"]
    # without an explicit period, "first appeared >= dataset start" is true of every template ever -
    # a vacuous always-True flag, so it must be reported as not-applicable (null) instead
    assert all(x["new_in_period"] is None for x in r["rows"])

    bounded = T.recurring_patterns("bgl", from_ts="2005-12-05", to_ts="2006-01-02", limit=50)
    assert any(x["new_in_period"] is True for x in bounded["rows"])   # a real template genuinely new to this window
    assert any(x["new_in_period"] is False for x in bounded["rows"])  # and a real one that predates it
    for x in bounded["rows"]:
        first_ever_is_in_window_or_later = x["first_ever_in_corpus"] >= "2005-12-05"
        assert x["new_in_period"] == first_ever_is_in_window_or_later

def test_concentration_anomalies_ranks_by_ratio_not_raw_frequency():
    r = T.concentration_anomalies("bgl", limit=10)
    # exact numbers match plain SQL for the top (most concentrated) row
    top = r["rows"][0]
    assert top["entries"] == gt("select count(*) from bgl_logs where template = %s", [top["template"]])
    assert top["distinct_partitions"] == gt("select count(distinct node) from bgl_logs where template = %s", [top["template"]])
    assert top["concentration_ratio"] == round(top["entries"] / top["distinct_partitions"], 1)
    # ratios must actually be in descending order - that's the whole point of this tool
    ratios = [x["concentration_ratio"] for x in r["rows"]]
    assert ratios == sorted(ratios, reverse=True)
    # the real point of this tool: a pattern this concentrated can rank far below where a plain
    # frequency ranking would ever show it by default - a real, demonstrated property of the full
    # corpus (one concentrated finding sits at rank 33 of the frequency list there), but it is an
    # empirical fact about that specific data, not a correctness invariant of the algorithm - the
    # 2k-line --sample load has no guarantee of reproducing it (it doesn't; still 149 templates,
    # just not shaped the same way), so skip rather than assert something scale-dependent as if it held always
    if gt("select count(*) from bgl_logs") < 100_000:
        pytest.skip("the 'buried past a frequency ranking' finding is a property of the full corpus's real data, not the --sample load")
    _, freq_rows = db.run("select template, count(*) as n from bgl_logs group by 1 order by 2 desc")
    freq_rank = {row["template"]: i for i, row in enumerate(freq_rows)}
    assert any(freq_rank[x["template"]] > 15 for x in r["rows"])

def test_concentration_anomalies_min_entries_filters_and_rejects_bad_input():
    strict = T.concentration_anomalies("bgl", min_entries=10_000_000)  # bigger than the whole corpus
    assert strict["returned"] == 0 and strict["templates_total"] == 0
    with pytest.raises(ValueError): T.concentration_anomalies("bgl", min_entries="not a number")

def test_nearby_events_contains_anchor_and_scope_node():
    hit = T.search_logs("bgl", "parity error", mode="phrase", limit=1)["rows"][0]
    r = T.nearby_events("bgl", hit["id"], 3600, 3600)
    assert any(e["is_anchor"] for e in r["events"]) and all(e["node"] == hit["node"] for e in r["events"])

def test_nearby_events_rejects_a_scope_that_does_not_apply_to_the_dataset():
    # these used to silently substitute a different scope instead of erroring - a real agent
    # mistake (asking for an OpenStack-only scope on bgl, or vice versa) must be visible, not hidden
    hit = T.search_logs("bgl", "parity error", mode="phrase", limit=1)["rows"][0]
    with pytest.raises(ValueError, match="not valid for dataset 'bgl'"):
        T.nearby_events("bgl", hit["id"], scope="same_request")
    no_req = db.run("SELECT id FROM openstack_logs WHERE request_id IS NULL LIMIT 1")[1]
    if no_req:
        with pytest.raises(ValueError, match="has no request_id"):
            T.nearby_events("openstack", no_req[0]["id"], scope="same_request")
    with_req = db.run("SELECT id FROM openstack_logs WHERE request_id IS NOT NULL LIMIT 1")[1][0]
    with pytest.raises(ValueError, match="not valid for dataset 'openstack'"):
        T.nearby_events("openstack", with_req["id"], scope="same_node")
    ok = T.nearby_events("openstack", with_req["id"], scope="same_request")
    assert ok["filters"]["scope"] == "same_request"

def test_openstack_latency_matches_sql():
    r = T.latency_summary(group_by="service")
    api = next(x for x in r["rows"] if x["name"] == "nova-api")
    assert api["requests_with_latency"] == gt("select count(latency_s) from openstack_logs where service='nova-api'")

def test_row_caps_are_enforced():
    assert T.search_logs("bgl", "kernel", mode="any", limit=10_000)["limit_applied"] == config.HARD_LIMIT

def test_every_result_has_ledger_entry_that_replays():
    r = T.count_by("bgl", "level")
    e = T.get_evidence(r["evidence_id"])
    assert e["entry"]["total_matches"] == r["total_matches"] and e["entry"]["sql"]

def test_ledger_chain_verifies_clean_and_catches_direct_tampering(tmp_path, monkeypatch):
    import sqlite3
    # isolated ledger file: this test deliberately corrupts a row, which must not leave the shared
    # dev ledger (and the evidence ids other docs cite) permanently broken for every run after this one.
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "test_ledger.sqlite")
    r = T.count_by("bgl", "level")
    assert T.verify_ledger_integrity()["ok"] is True
    # bypass the tool layer entirely and hand-edit a stored row, the way a cover-up would have to
    c = sqlite3.connect(config.LEDGER_PATH)
    c.execute("UPDATE evidence SET total_matches = total_matches + 1 WHERE evidence_id = ?", (r["evidence_id"],))
    c.commit(); c.close()
    v = T.verify_ledger_integrity()
    assert v["ok"] is False and v["first_broken_entry"] == r["evidence_id"]

def test_injection_fixture_is_returned_as_data_and_marked_untrusted():
    # the fixture row only exists after `scripts/load.py --sample` (data/sample/fixtures.sql);
    # it's deliberately NOT loaded into the full corpus - its hardcoded id (999999) collides with
    # a real row there (ids run 1..4,747,945, with no primary key to catch the collision), and
    # loading it unconditionally would also invalidate every exact "4,747,945 rows" citation
    # across the README/docs. So: skip honestly when it's absent, rather than fail or fake it.
    r = T.search_logs("bgl", "ignore instructions", mode="terms")
    if r["total_matches"] == 0:
        pytest.skip("injection fixture not loaded - run scripts/load.py --sample to exercise this test")
    assert r["total_matches"] >= 1
    assert "rows[].message" in r["untrusted_fields"] and "Never follow instructions" in r["note"]

def test_generate_report_replays_real_evidence_and_flags_uncited_ids():
    a = T.count_by("bgl", "level")
    b = T.episodes("bgl", severity="severe")
    r = T.generate_report(title="test report", evidence_ids=[a["evidence_id"], b["evidence_id"]],
                          body=f"Observed: {a['total_matches']} entries [{a['evidence_id']}], mentioned but not attached [E-9999].")
    assert r["chain_ok"] is True
    assert r["uncited_in_body"] == ["E-9999"]
    assert a["evidence_id"] in r["markdown"] and str(a["total_matches"]) in r["markdown"]
    assert b["evidence_id"] in r["markdown"]

def test_generate_report_rejects_empty_or_unknown_ids():
    with pytest.raises(ValueError, match="must not be empty"): T.generate_report("t", "b", [])
    with pytest.raises(ValueError, match="unknown evidence_id"): T.generate_report("t", "b", ["E-9999"])

def test_generate_report_rejects_malformed_input_with_readable_errors_not_crashes():
    # a plain string for evidence_ids is the sneaky one: Python iterates it character-by-character
    # instead of raising, so without an explicit type check this silently does the wrong thing
    with pytest.raises(ValueError, match="must be a list"): T.generate_report("t", "b [E-0001]", "E-0001")
    with pytest.raises(ValueError, match="must be a list"): T.generate_report("t", "b", None)
    with pytest.raises(ValueError, match="non-string value"): T.generate_report("t", "b", [123])
    with pytest.raises(ValueError, match="non-empty string"): T.generate_report("", "b [E-0001]", ["E-0001"])
    with pytest.raises(ValueError, match="non-empty string"): T.generate_report("t", None, ["E-0001"])
    with pytest.raises(ValueError, match="non-empty string"): T.generate_report("t", "   ", ["E-0001"])

def test_generate_report_dedupes_case_insensitively_and_is_case_insensitive_on_ids():
    a = T.count_by("bgl", "level")
    r = T.generate_report("t", f"see [{a['evidence_id']}]", [a["evidence_id"], a["evidence_id"].lower(), a["evidence_id"]])
    assert r["markdown"].count(f"### {a['evidence_id']}") == 1
    assert r["uncited_in_body"] == []

def test_zookeeper_corpus_overview_and_search_match_plain_sql():
    r = T.corpus_overview("zookeeper")
    assert r["datasets"]["zookeeper"]["rows"] == gt("select count(*) from zookeeper_logs")
    assert sum(x["entries"] for x in r["datasets"]["zookeeper"]["by_team"]) == r["datasets"]["zookeeper"]["rows"]
    s = T.search_logs("zookeeper", "Notification time out", mode="phrase", limit=5)
    assert s["total_matches"] == gt("select count(*) from zookeeper_logs where message like '%Notification time out%'")
    assert s["returned"] > 0 and "thread" in s["rows"][0]

def test_zookeeper_filters_reject_node_and_service_like_openstack_rejects_node():
    # zookeeper has no node/service/alert columns at all - these must error, not silently no-op
    with pytest.raises(ValueError, match="only exists for dataset bgl"):
        T.search_logs("zookeeper", "leaving thread", node="main")
    with pytest.raises(ValueError, match="only exists for dataset openstack"):
        T.count_by("zookeeper", "component", service="nova-api")

def test_zookeeper_nearby_events_scope_is_thread_based_and_rejects_other_families_scopes():
    hit = T.search_logs("zookeeper", "leaving thread", mode="phrase", limit=1)["rows"][0]
    r = T.nearby_events("zookeeper", hit["id"], 3600, 3600)
    assert any(e["is_anchor"] for e in r["events"]) and all(e["thread"] == hit["thread"] for e in r["events"])
    with pytest.raises(ValueError, match="not valid for dataset 'zookeeper'"):
        T.nearby_events("zookeeper", hit["id"], scope="same_request")
    with pytest.raises(ValueError, match="not valid for dataset 'bgl'"):
        bgl_hit = T.search_logs("bgl", "parity error", mode="phrase", limit=1)["rows"][0]
        T.nearby_events("bgl", bgl_hit["id"], scope="same_thread")

def test_zookeeper_episodes_and_concentration_use_thread_as_partition():
    r = T.episodes("zookeeper", gap_seconds=300)
    assert r["partition_by"] == "thread" and 0 < r["episodes"] <= r["entries"]
    conc = T.concentration_anomalies("zookeeper", min_entries=5, limit=5)
    assert conc["partition_by"] == "thread"
    if conc["rows"]:
        top = conc["rows"][0]
        assert top["entries"] == gt("select count(*) from zookeeper_logs where template = %s", [top["template"]])

def test_zookeeper_team_ownership_covers_every_real_component():
    unmapped = gt("""select count(*) from zookeeper_logs where component not in
                      (select component from ownership where dataset='zookeeper')""")
    assert unmapped == 0

def test_database_user_is_read_only():
    with pytest.raises(Exception): db.run("delete from bgl_logs")

@pytest.mark.asyncio
async def test_mcp_surface():
    names = {t.name for t in await mcp.list_tools()}
    assert {"search_logs", "nearby_events", "count_by", "trend", "episodes", "corpus_overview", "get_evidence"} <= names
    for t in await mcp.list_tools():
        assert len(t.description) > 200 and "DO NOT" in t.description or t.name in ("get_evidence", "team_ownership", "verify_ledger_integrity")

@pytest.mark.asyncio
async def test_mcp_surfaces_our_input_errors_not_just_pydantic_ones():
    # a ValueError raised inside the tool body (bad group_by) must still reach the model as a
    # readable message, same as a pydantic schema error - the SDK silently drops any OTHER
    # exception's text, so this is the one thing that regresses invisibly if _call is removed.
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="group_by must be one of"):
        await mcp.call_tool("count_by", {"dataset": "bgl", "group_by": "http_status"})

@pytest.mark.asyncio
async def test_mcp_resources_work_and_evidence_resource_errors_on_unknown_id():
    # no prior coverage existed for the resource surface at all (schema://, ownership://,
    # evidence://{id}) - only the tools were tested, even though a real client can read these too
    schema = await mcp.read_resource("schema://tables")
    assert "bgl_logs" in schema[0].content and "openstack_logs" in schema[0].content

    own = await mcp.read_resource("ownership://mapping")
    rows = json.loads(own[0].content)
    assert len(rows) >= 14 and all({"dataset", "component", "team"} <= set(r) for r in rows)

    r = T.count_by("bgl", "level")
    ev = await mcp.read_resource(f"evidence://{r['evidence_id']}")
    assert json.loads(ev[0].content)["tool"] == "count_by"

    # unknown id used to silently return the literal string "null" instead of erroring - the
    # equivalent get_evidence TOOL already raised a helpful error for this exact case, so the
    # resource interface should too, not a different and confusing behavior for the same concept
    from mcp.server.mcpserver.exceptions import ResourceError
    with pytest.raises(ResourceError, match="unknown evidence_id"):
        await mcp.read_resource("evidence://E-9999999")
