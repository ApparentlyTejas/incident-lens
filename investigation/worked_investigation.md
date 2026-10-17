# Worked investigation: ciod job-launch failures ("invalid or missing program image")

> Every number below comes from a real tool call; its evidence id `[E-xxxx]` can be replayed with `get_evidence` or the `evidence://` resource to see the exact SQL and full result. Data scope: BGL full corpus, 2005-06-03 to 2006-01-04 (4,747,945 rows `[E-0007]`). OpenStack appears in the whole-corpus count in section 3 (`corpus_overview`, `[E-0007]`).

## 0. Why this pattern

`ciod: Error loading <path>: invalid or missing program image, ...` is BGL's job-launch-control daemon (`ciod`) refusing to start a parallel job because the executable it was told to run isn't there or isn't runnable. It fits all four selection criteria: the text is directly searchable (`search_logs`), it recurs as 52,763 separate episodes rather than one long outage (`episodes`, `[E-0005]`), it hits 1,024 distinct nodes (`count_by`, `[E-0003]`), and it has a sharp, visible trend with one dramatic spike week (`trend`, `[E-0004]`). It also comes from many different users and job paths (`/home/yates/...`, `/home/spelce1/...`, `/bgl/apps/...`), so it reads as a recurring *class* of job-submission problem, not one person's mistake.

## 1. Engineer

**Question:** Find messages about "invalid or missing program image" in BGL between 2005-06-01 and 2006-02-01; what happened around the first one, and is it always the same node?

**Tool calls:** `search_logs` `[E-0001]` -> `nearby_events` `[E-0002]` -> `count_by(group_by=node)` `[E-0003]`

**Executed queries:** see `get_evidence("E-0001")` / `E-0002` / `E-0003` for the exact SQL that ran.

**Answer:**

- **Observed.** `search_logs` finds 112,584 matching entries in the stated period, truncated to a 3-row sample `[E-0001]`. The first hit is id 4988, timestamp 2005-06-03 16:17:40.190183, node `R26-M0-NC-I:J18-U11`, component `APP`, level `FATAL`, message `ciod: Error loading /p/gb2/stella/RAPTOR/65641/raptor: invalid or missing program image, No such file or directory` `[E-0001]`. `nearby_events` around that entry (±5 seconds, any node) shows 32 matching events in that 10-second window, with 10 returned `[E-0002]`: the same message, same job (`raptor`, job id 65641), firing on different nodes within tens of milliseconds of each other, for example `R26-M0-NC-I:J18-U01` and `R26-M0-N8-I:J18-U11` `[E-0002]`. `count_by(node)` over the full period confirms it is not one bad node: 1,024 distinct nodes are affected, each with a similar share of about 0.16%, for example 178 entries each for the top five nodes shown `[E-0003]`.
- **Hypotheses.** The simultaneous multi-node firing pattern is consistent with one parallel job launch (ciod starts the same executable on every node of a partition at once) failing identically everywhere because the executable path was never valid in the first place - a missing build, a bad path, or a job submitted before its binary finished copying to shared storage. This is a hypothesis about the mechanism (why it fails on all nodes at once), not a claim about why any specific path was wrong.
- **Recommended check.** Before the next job of this kind is submitted, verify the executable path exists and is readable from every node's view of shared storage (the same filesystem mount the compute nodes use, not just the submit/login node) - that would directly test the leading hypothesis.
- **Unknown.** Why the specific path was invalid for any given job (typo, incomplete build, storage outage) is not recoverable from these log lines alone.

## 2. Team lead

**Question:** How often does this recur, is it getting worse, and how many separate episodes is that versus raw entries?

**Tool calls:** `trend(week)` `[E-0004]` -> `episodes` `[E-0005]` -> `count_by(team)` `[E-0006]`

**Answer:**

- **Observed.** Over 2005-06-01 to 2006-02-01, 112,584 entries match, as 52,763 episodes `[E-0005]` (gap rule: entries on the same node 300 seconds or less apart are one episode `[E-0005]`). Entries considerably outnumber episodes (112,584 vs 52,763 `[E-0005]`), so this is genuinely repeated bursts, not one giant outage recounted many times. The weekly trend is sharply uneven: most weeks run in the hundreds to low thousands, but the week of 2005-08-08 alone has 24,064 entries, a 9,300% jump over the prior week `[E-0004]`. The largest single-node episode is 20 entries over 2,114 seconds, on node `R56-M1-N4-I:J18-U11` starting 2005-08-12 `[E-0005]`. `count_by(team)` attributes 100% of these entries to Application Support via the component `APP` and the (fictional) ownership table `[E-0006]`.
- **Hypotheses.** The 2005-08-08 spike suggests a specific batch of jobs (likely one script or one user's submission loop) hit the same bad-path problem repeatedly in a short window, rather than a steady background rate of unrelated mistakes.
- **What to investigate first, and who owns it.** Application Support should look at what was submitted in the week of 2005-08-08 first, since that single week accounts for over a fifth of the entire 7-month total `[E-0004]` - the highest-leverage single data point in the whole period.
- **Unknown.** Whether the 2005-08-08 batch was one user, one automated pipeline, or several unrelated submissions landing in the same week cannot be determined from these fields alone; there is no user/job-owner column in the parsed BGL schema.

## 3. Manager

**Question:** Of the recurring problems in BGL, which deserves attention first right now?

**Tool calls:** `corpus_overview` `[E-0007]` -> `recurring_patterns` `[E-0008]` -> per-candidate `trend` / `episodes` / `count_by` over the most recent 4-week window in the data, 2005-12-05 to 2006-01-02: ciod `[E-0009, E-0010, E-0011]`, data TLB error interrupt `[E-0012, E-0013, E-0014]`, idoproxydb ASSERT `[E-0015, E-0016, E-0017]`.

**Answer:**

`recurring_patterns` over the full period ranks the ciod program-image template among the most frequent FATAL message templates, alongside "data TLB error interrupt" (152,734 entries, 541 nodes) and "idoproxydb hit ASSERT condition" (84,168 entries, but only 1 node) `[E-0008]`. Lifetime frequency alone doesn't say what's current, so the ranking below uses each candidate's most recent four weeks of data (2005-12-05 to 2006-01-02) rather than its all-time total `[E-0009, E-0012, E-0015]` - a problem that has already stopped recurring is a lower priority than one still growing, even if its lifetime count is larger.

Priority = episodes_in_window x components_affected x (1 + growth_vs_prior_window):

| Problem | Episodes (last 4 wks) | Components | Growth vs prior 4 wks | Priority |
|---|---|---|---|---|
| ciod job-launch failures (program image) | 6,019 `[E-0010]` | 1 `[E-0011]` | +224% `[E-0009]` | 19,502 (6,019 x 1 x 3.24, from `[E-0009, E-0010, E-0011]`) |
| data TLB error interrupt | 0 `[E-0013]` | 0 `[E-0014]` | -100% `[E-0012]` | 0 |
| idoproxydb ASSERT | 0 `[E-0016]` | 0 `[E-0017]` | -100% `[E-0015]` | 0 |

This priority score ranks observed frequency, spread and recent trend only - it is not an impact, downtime, or cost estimate.

1. **ciod job-launch failures** - still active and accelerating: 18,608 entries in the most recent window of the log alone, up 224% versus the four weeks before that `[E-0009]`. The other two candidates produced zero matching entries in that same recent window `[E-0012, E-0015]` - they have already stopped recurring. This is the only one of the three still growing at the end of the observed period.
2. **data TLB error interrupt** and **idoproxydb ASSERT** - high lifetime totals `[E-0008]`, but inactive in the most recent window of data `[E-0012, E-0015]`; worth a retrospective, not immediate action.

**Recommended action:** Application Support should review job-submission tooling and path validation for the recurring pattern behind priority #1, starting with whatever drove the 2005-08-08 spike (the team-lead section above) and whatever is driving the current growth shown in `[E-0009]`.

## 4. What the data does and does not establish

- **Establishes:** how often this fails (entries and episodes, with the gap rule stated), how widely it is spread (1,024 of the system's nodes `[E-0003]`), that many distinct jobs and paths are involved, that it is still active and growing in the most recent window of the dataset `[E-0009]`, and which team owns the affected component under our fictional ownership mapping `[E-0006]`.
- **Does not establish:** why any individual job's path was wrong, who submitted the jobs behind the 2005-08-08 spike, any downtime caused, or any financial or customer impact. BGL is a research supercomputer log with no customer-facing or cost data, and none is invented here.
