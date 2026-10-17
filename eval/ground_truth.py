#!/usr/bin/env python3
"""Plain-SQL ground truth for the golden numbers (no index, no tool code). Writes eval/ground_truth.json.
Compare these to the numbers in Claude's answers / ledger entries."""
import json, sys, pathlib, yaml
ROOT = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
from server import db
P = yaml.safe_load((ROOT / "eval/params.yaml").read_text())
SEV = "('FATAL','FAILURE','SEVERE','ERROR')"
like = f"%{P['term']}%"
# the real tool's inverted index is word-tokenized with no stemming, so phrase search on
# "parity error" correctly excludes "parity errors" (plural) - a plain ILIKE substring match
# does NOT exclude it, so it silently over-counts by the plural rows unless excluded here too
# (tests/test_tools.py's ground truth already does this; this file was missing the same fix).
like_excl = f"%{P['term']}s%"
gt = {
 "E1_total_matches": db.scalar("select count(*) from bgl_logs where message ilike %s and message not ilike %s and ts >= %s and ts < %s", [like, like_excl, P["from"], P["to"]]),
 "E3_matches_by_node_top5": db.run("select node, count(*) as entries from bgl_logs where message ilike %s and message not ilike %s and ts >= %s and ts < %s group by 1 order by 2 desc limit 5", [like, like_excl, P["from"], P["to"]])[1],
 "L1_severe_by_team": db.run(f"select coalesce(o.team,'UNMAPPED') as team, count(*) as entries from bgl_logs l left join ownership o on o.dataset='bgl' and o.component=l.component where l.level in {SEV} and l.ts >= %s and l.ts < %s group by 1 order by 2 desc", [P["from"], P["to"]])[1],
 "L2_severe_by_week": db.run(f"select date_trunc('week', ts) as week, count(*) as entries from bgl_logs where level in {SEV} and ts >= %s and ts < %s group by 1 order by 1", [P["from"], P["to"]])[1],
 "L3_severe_entries": db.scalar(f"select count(*) from bgl_logs where level in {SEV} and ts >= %s and ts < %s", [P["from"], P["to"]]),
 "L4_openstack_by_service": db.run("select service, count(*) as entries, count(latency_s) as requests_with_latency, quantile_cont(latency_s,0.95) as p95_s, sum(case when level in ('ERROR','CRITICAL') then 1 else 0 end) as error_entries from openstack_logs group by 1 order by 2 desc")[1],
 "M3_rows_by_dataset": {"bgl": db.scalar("select count(*) from bgl_logs"), "openstack": db.scalar("select count(*) from openstack_logs")},
 "M3_bgl_by_team": db.run("select coalesce(o.team,'UNMAPPED') as team, count(*) as entries from bgl_logs l left join ownership o on o.dataset='bgl' and o.component=l.component group by 1 order by 2 desc")[1],
}
(ROOT / "eval/ground_truth.json").write_text(json.dumps(gt, indent=2, default=str))
print(json.dumps({k: v for k, v in gt.items() if not isinstance(v, list)}, default=str)); print("wrote eval/ground_truth.json")
