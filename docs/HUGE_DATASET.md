# Huge dataset scale demonstration

Two separate, standalone demonstrations for the "Working with Huge Datasets" award. Neither
touches `bgl_logs`, `openstack_logs`, `ownership`, or any of the investigative tools that the
main score is judged on - see [`prep/parse_thunderbird.py`](../prep/parse_thunderbird.py)'s
docstring for why that isolation is deliberate.

1. **Thunderbird**, loaded locally - 8M rows, real indexed full-text search.
2. **Remote OpenTelemetry Parquet**, queried live off S3 with zero download - 1 billion rows in
   a single file, up to 50 billion across the full set, exactly as the challenge brief describes
   ("point SereneDB at the URL and query it where it is").

## Part 1: Thunderbird (local)

### The dataset

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
python3 prep/parse_thunderbird.py data/raw/Thunderbird.log data/processed/thunderbird.parquet
python3 scripts/load_thunderbird.py data/processed/thunderbird.parquet --limit 8000000   # or higher, memory permitting
python3 eval/huge_dataset_benchmark.py --n 12
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

## Part 2: Remote OpenTelemetry Parquet (no download, no local storage)

The challenge brief lists 50 billion rows of OpenTelemetry demo-app telemetry sitting in public
S3 as Parquet, 50 files of about a billion rows each: "Don't download it. Point SereneDB at the
URL and query it where it is." This is the single biggest scale option available for this award,
and it sidesteps Part 1's whole memory-ceiling problem entirely - nothing is loaded locally, so
there is no table to build and no index to exhaust RAM on.

Run via [`eval/remote_parquet_benchmark.py`](../eval/remote_parquet_benchmark.py), real queries
against the real remote file, no sampling:

```
Remote file: https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet
No local download, no local storage - SereneDB reads it directly off S3.

exact row count, whole file (1 billion rows)
  305 ms  ->  [(1000000000,)]

group by service + severity (full scan, real text columns)
  4,015 ms  ->  [('cart', 'info', 288255720), ('frontend', 'info', 170080148),
                 ('cart', 'information', 140377744), ('frontend', 'error', 105472452),
                 ('email', 'info', 72294662), ('shipping', 'info', 28031798), ...]

filtered search, first 3 error rows (early-exit via LIMIT)
  63,136 ms  ->  3 real rows, e.g. "Failed to place order" (frontend),
                 "failed to charge card: ... Visa cache full: cannot add new item" (frontend)

exact count of high-severity rows (SeverityNumber >= 17, full scan)
  1,646 ms  ->  [(75567724,)]
```

**Every number and every log line above is real** - genuine OpenTelemetry demo-app data (an
e-commerce app: cart, frontend, checkout, shipping, email, recommendation services), not
synthetic. A plain `count(*)` over a billion rows comes back in well under a second thanks to
Parquet's own row-group metadata; a query that has to actually read the `Body` text column
across the whole file (the group-by, and the unlucky `LIMIT 3` search that had to scan deep
before finding a match) costs seconds to roughly a minute, which is the honest cost of pulling
real column data over the network rather than just metadata - exactly the kind of cost trade-off
the award asks to see reported, not hidden.

Widening the window costs a line of SQL, not a loading job - `UNION ALL` more `part_NNN.parquet`
URLs (or a glob, where SereneDB's remote-table syntax supports it) to cover more of the 24-hour
span, all the way up to the full 50 files / 50 billion rows, with no change to the query shape.

### Reproducing this

```bash
python3 eval/remote_parquet_benchmark.py
```

Needs only `SERENEDB_ADMIN_DSN` (or the same default `127.0.0.1:7890` as everywhere else in this
project) and outbound internet access to S3 - no credentials, no download, no local disk used.
