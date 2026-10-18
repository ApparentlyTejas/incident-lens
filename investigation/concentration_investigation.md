# Worked investigation: a hardware signal a frequency-sorted view buries

> Every number below comes from a real tool call; its evidence id `[E-xxxx]` can be replayed with `get_evidence` or the `evidence://` resource to see the exact SQL and full result. Data scope: BGL full corpus, 2005-06-03 to 2006-01-04 (4,747,945 rows `[E-2897]`).

## 0. Why this pattern - and an honest false start

`concentration_anomalies` ranks templates by how confined they are to one node, not by how often they fire. Run over the whole BGL corpus with `min_entries=50`, the top-ranked result by concentration ratio is `idoproxydb hit ASSERT condition...` - 84,168 entries, reported as confined to exactly 1 partition `[E-2898]`.

That one doesn't hold up on inspection. Every one of those entries has `node = 'NULL'` `[E-2885]` - not a real physical node, but BGL's own placeholder for missing per-node attribution (the same caveat `episodes` already surfaces: *"episodes on 'NULL' merge unrelated sources and should not be read as one node's burst"*). A concentration ratio built on a missing-data placeholder isn't a hardware finding, it's a data-quality artifact, and presenting it as "go check that one node" would be exactly the kind of invented precision this project's whole design is built to avoid.

The **second**-ranked result is the real finding: `MACHINE CHECK DCR read timeout (mc=e08x iar <*> lr <*>)` - 14,481 entries, confined to exactly 1 real node, `R26-M0-N0-I:J18-U11` `[E-2898]`. By plain frequency this same template ranks 33rd out of 23,484 templates `[E-2899]` - well past any normal top-10 or top-20 view - first by concentration. This is the one worth three roles' worth of attention.

## 1. Engineer

**Question:** Find "MACHINE CHECK DCR read timeout" in BGL; is it confined to one node, and what else was happening around the first hit?

**Tool calls:** `search_logs` `[E-2890]` -> `nearby_events` `[E-2892]` -> `count_by(group_by=node)` `[E-2891]`

**Answer:**

- **Observed.** `search_logs` (phrase mode) finds 14,481 matching entries in the full corpus, truncated to a 3-row sample `[E-2890]`. The first hit is id 4239028, timestamp 2005-11-21 06:40:44.344630, node `R26-M0-N0-I:J18-U11`, level `INFO`, message `MACHINE CHECK DCR read timeout (mc=e08x iar 0x00000000 lr 0xc00045a4)` `[E-2890]`. The next two rows, both within a second of the first, are the identical message on the identical node - a tight repeat, not a one-off. `nearby_events` around the first hit (any node, a 10-second window centered on it) shows 34 matching events in that window `[E-2892]`, all the same message. `count_by(node)` over the whole corpus confirms it: all 14,481 entries are on `R26-M0-N0-I:J18-U11`, a 100% share, and no other node has a single occurrence `[E-2891]`.
- **Hypotheses.** A DCR (Device Control Register) read timeout repeating at sub-second intervals, on one node only, with identical `iar`/`lr` values each time, reads as a stuck or failing hardware register read path on that specific node - not a software race or a workload-triggered condition (those would be expected to show up on more than one node running the same code).
- **Recommended checks/actions.** Pull `R26-M0-N0-I:J18-U11`'s hardware diagnostic/service log for the window this burst covers `[E-2890]` and cross-reference against any BGL service-action record for that midplane; if the node was later swapped or serviced, that would confirm the hardware-fault reading.
- **Unknown.** Whether this caused a job failure or was silently retried; the logs show the symptom repeating, not its downstream effect.

## 2. Team Lead

**Question:** Is "MACHINE CHECK DCR read timeout" a recurring problem, and which team owns it?

**Tool calls:** `trend(week)` `[E-2894]` -> `episodes` `[E-2895]` -> `count_by(team)` `[E-2896]`

**Answer:**

- **Observed.** `trend` by week over the full corpus shows matching entries in exactly one week bucket, with all 14,481 entries landing there and zero in every other week `[E-2894]`. `episodes` (300-second gap rule, same node) collapses those same 14,481 entries into exactly 1 episode `[E-2895]` - one continuous burst, not a recurring pattern spread over time. `count_by(team)` attributes all of these entries to **Kernel & Runtime**, a full share, via the `component` ownership mapping `[E-2896]`.
- **Hypotheses.** This reads as a one-time hardware event, not an ongoing recurring problem in the "fires every week" sense - the entry count comes entirely from one node logging the same symptom repeatedly during one burst, not from the fault recurring across separate incidents.
- **Recommended checks/actions.** Kernel & Runtime should treat this as a single hardware-incident ticket for one node, not a recurring-pattern backlog item; the right investigation is "what happened to this node during that burst," not "why does this keep happening."
- **Unknown.** Whether `R26-M0-N0-I:J18-U11` had other, lower-volume symptoms before or after this window that the episode gap rule wouldn't connect to this one.

## 3. Manager

**Question:** Of everything `concentration_anomalies` flags, what's worth prioritizing, and why doesn't a frequency-sorted view already show it?

**Tool calls:** `corpus_overview` `[E-2897]` -> `concentration_anomalies` `[E-2898]` -> `recurring_patterns`, for the raw-frequency contrast `[E-2899]`

**Answer:**

- **Observed.** The full corpus is 4,747,945 BGL rows, 2005-06-03 to 2006-01-04 `[E-2897]`. `concentration_anomalies` ranks `MACHINE CHECK DCR read timeout` second overall by concentration ratio `[E-2898]` - the top-ranked result is a data-quality artifact (§0, missing node attribution), making this the highest-confidence genuine single-node signal in the corpus. The same template ranks 33rd of 23,484 by plain frequency `[E-2899]` - below the cutoff of any top-10 or top-20 view a team would normally scan.
- **Hypotheses.** A frequency-sorted view is the natural first thing anyone reaches for, and it's exactly the view that hides this finding - every template ranked above it by raw count is more spread out, none of them confined to a single piece of hardware the way this one is.
- **Recommended checks/actions.** Prioritize a hardware check on `R26-M0-N0-I:J18-U11` ahead of several higher-raw-count-but-more-spread patterns, specifically because it's concentrated: a problem confined to one node is cheaper to confirm and fix than one spread across hundreds, and concentration is itself evidence against "normal background noise."
- **Unknown.** Downtime, job impact, or financial cost of this node's incident - the logs establish the symptom's frequency and exact confinement, nothing about its consequences.
