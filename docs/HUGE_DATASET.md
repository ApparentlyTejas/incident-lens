# Huge dataset scale demonstration (Thunderbird)

This is a deliberately separate, standalone demonstration for the "Working with Huge Datasets"
award. It never touches `bgl_logs`, `openstack_logs`, `ownership`, or any of the investigative
tools that the main score is judged on - see [`prep/parse_thunderbird.py`](../prep/parse_thunderbird.py)'s
docstring for why that isolation is deliberate.

## The dataset

[Thunderbird](https://zenodo.org/records/8196385) (Loghub / Sandia National Labs) - the same HPC
supercomputer-log family as BGL, but a different, syslog-style line format with no explicit level
field. Per the challenge brief's own dataset table: **211,212,192 lines, 29.60 GB compressed,
244 days** of coverage - the largest option listed alongside Windows (114.6M lines). The raw
extracted text file measures 31.8 GB on disk; the archive itself is the 29.60 GB figure.

Parsed with [`prep/parse_thunderbird.py`](../prep/parse_thunderbird.py):

```
thunderbird: 211,210,954 rows parsed, 1,238 skipped -> data/processed/thunderbird.parquet
real    76m34s
```

**99.9994% parse success rate** over the full file, run cold with no shortcuts. The 1,238 skipped
lines fail the same defensive checks `parse_bgl.py` already uses on BGL (too few fields, an
unparseable timestamp) - not guessed at, counted and excluded.

## Honest disclosure: what actually got loaded, and why

The full 211M parsed rows were **not** all loaded into SereneDB. Two real attempts to build the
inverted index over that many rows (and, separately, over 10-15M rows) ended in SereneDB being
OOM-killed on this specific development machine, which has 8 GB of physical RAM shared with Docker
Desktop, this session's other containers, and everything else running on it at the time. The table
load itself succeeded up to 20M rows in seconds; it was the `CREATE INDEX ... USING inverted` step
that exhausted memory.

Rather than keep re-attempting a crash/restart loop against a hard resource ceiling, the dataset
was loaded at a **verified-stable 8,000,000 rows** - repeatable, clean, no crash, confirmed by
reloading from scratch after a restart. This is still a real, substantial scale demonstration
(8M rows, loaded and indexed from Parquet in under 10 seconds total), and it's an honest number:
the full 211M-row Parquet file is the actual artifact this was built from, and the loader
(`scripts/load_thunderbird.py --limit N`) will load more of it on a machine with more memory to
give it. The row-count ceiling here is a property of this laptop, not of SereneDB or this project's
approach.

## Real query times (8,000,000 rows, `thunderbird_logs` + `thunderbird_idx`)

Run via [`eval/huge_dataset_benchmark.py`](../eval/huge_dataset_benchmark.py), 12 calls per query,
cold call excluded, min/median/p95/max reported - same methodology as
[`eval/benchmark_results.md`](../eval/benchmark_results.md) uses for the main corpus:

```
thunderbird_logs: 8,000,000 rows

query                                           min   median      p95      max  (ms, n=12)
exact row count (whole table)                  0.3      0.6      2.4      3.7
full-text search, common term                  0.9      1.2      1.4      2.2
full-text search + time range                  0.9      1.2      1.5      1.7
group by node (top 20)                         9.7     10.1     13.0     13.4
group by component (top 20)                    7.2      8.1     11.7     12.7
distinct nodes                                 5.3      5.5      5.9      6.2
labelled-alert rows                            3.4      3.4      3.7      4.0
week-bucketed trend                           11.5     12.3     15.2     15.4
```

Every number above is a real query against the real loaded table, not a sample or an estimate.
Full-text search stays sub-2ms even at this scale; the group-by/trend aggregations (which scan and
bucket every row) stay under 16ms.

## Reproducing this

```bash
python prep/parse_thunderbird.py data/raw/Thunderbird.log data/processed/thunderbird.parquet
python scripts/load_thunderbird.py data/processed/thunderbird.parquet --limit 8000000   # or higher, memory permitting
python eval/huge_dataset_benchmark.py --n 12
```

## MCP connection

No separate server or port - `thunderbird_logs`/`thunderbird_idx` live in the same SereneDB
instance as the main corpus, reachable with the same `il_reader` role used everywhere else in this
project (`SERENEDB_DSN`, see the main [README](../README.md#quickstart-clean-checkout)). It is not
wired into the MCP tool surface or the role prompts on purpose (see the isolation rationale above),
so querying it is plain SQL through that same connection, for example:

```bash
psql "host=127.0.0.1 port=7890 user=il_reader password=il_reader dbname=postgres" \
  -c "SELECT count(*) FROM thunderbird_idx WHERE message @@ ts_phrase('kernel panic')"
```
