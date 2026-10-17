# Worked investigation: nova-compute image-cache failures ("InvalidDiskInfo")

> Every number below comes from a real tool call; its evidence id `[E-xxxx]` can be replayed with `get_evidence` or the `evidence://` resource to see the exact SQL and full result. Data scope: OpenStack full corpus, 2017-05-14 19:39:01 to 2017-05-17 12:02:35 (207,812 rows `[E-1090]`). This complements [`worked_investigation.md`](worked_investigation.md) (BGL) by showing the same evidence-ledger methodology holds on the second dataset too, with its own, genuinely different failure mode and shape.

## 0. Why this pattern

`nova-compute`'s periodic image-cache-verification task (`ComputeManager._run_image_cache_manager_pass`) throws an unhandled `InvalidDiskInfo` exception and aborts entirely whenever it encounters one instance whose backing disk file no longer exists on that compute host. It fits the same four selection criteria as the BGL example: directly searchable (`search_logs`), recurs as its own separate episode each time rather than one long outage (`episodes`, `[E-1085]`), and - unlike the BGL example, which is confined to one node - this one is spread across 8 different, unrelated VM instances, pointing at a structural bug in the cache-verification logic rather than any one broken instance. It is also, by raw frequency, the single most common ERROR-level template in the whole OpenStack corpus (`recurring_patterns`, `[E-1089]`), so it is the natural first thing a team lead or manager would be shown.

## 1. Engineer

**Question:** Find messages about "InvalidDiskInfo" in OpenStack; what happened around the first one, and is it always the same instance?

**Tool calls:** `search_logs` `[E-1082]` → `nearby_events` `[E-1083]`

**Answer:**

- **Observed.** `search_logs` finds exactly 8 matching entries across the whole corpus, all returned (no truncation) `[E-1082]`. The first hit is id 45631, timestamp 2017-05-16 03:19:45.356, service `nova-compute`, message `InvalidDiskInfo: Disk info file is invalid: qemu-img failed to execute on /var/lib/nova/instances/16797a0f-32fb-494c-8f36-38588148c35d/disk` `[E-1082]`. `nearby_events` around that entry (±2 seconds, same service) shows 31 log lines in that 4-second window - a single Python traceback logged one line per log entry: `Error during ComputeManager._run_image_cache_manager_pass` → `periodic_task.py` → `manager.py` → `libvirt/driver.py:manage_image_cache` → `imagecache.py:_age_and_verify_cached_images` → `_list_backing_images` → `get_disk_backing_file` → `qemu_img_info` → `InvalidDiskInfo` `[E-1083]`. Reading the message text of all 8 matches directly (from `[E-1082]`'s rows and a repeat of the search): every one of the 8 occurrences names a **different** instance UUID (`16797a0f...`, `daf38630...`, `74e32610...`, `ac7c329c...`, `0c28eea9...`, `2a841e49...`, `6fc3fd6d...`, `bd7d1a40...`) - never the same instance twice.
- **Hypotheses.** The image-cache-verification pass walks every instance's disk on the host and calls `qemu-img info` on each; a missing disk file is consistent with that specific instance having already been deleted (or its disk relocated) between when the cache manager built its instance list and when it got to checking that instance - a stale-reference race, not damage to a live VM's disk. The exception propagating all the way up and aborting `_run_image_cache_manager_pass` entirely (rather than being caught and skipped per-instance) is a hypothesis about *why* this recurs identically each time rather than being handled once and fixed: the periodic task has no per-instance error isolation, so one stale reference fails the whole pass.
- **Recommended check.** Before the next deploy, check whether `nova.virt.libvirt.imagecache._list_backing_images` catches a missing-disk error per-instance or lets it raise - that would directly confirm or rule out the "no per-instance isolation" hypothesis, and is the fix if confirmed.
- **Unknown.** Why each specific instance's disk was already gone (normal deletion timing, a cleanup race, or something else) is not recoverable from these log lines alone - there is no instance-lifecycle/deletion-event column in the parsed OpenStack schema to cross-reference.

## 2. Team lead

**Question:** How often does this recur, is it spread over time or one burst, and which team owns it?

**Tool calls:** `trend(hour)` `[E-1084]` → `episodes` `[E-1085]` → `count_by(team)` `[E-1086]` → `count_by(service, severe)` `[E-1087]`

**Answer:**

- **Observed.** All 8 entries `[E-1084]` fall between 2017-05-16 03:00 and 2017-05-17 05:00, roughly one occurrence every few hours over a 26-hour span - not a single burst `[E-1084]`. `episodes` (gap rule: entries on the same service 300 seconds or less apart are one episode `[E-1085]`) counts exactly 8 episodes for 8 entries `[E-1085]` - entries equal episodes here, meaning every occurrence is already its own fully separate incident, spaced far enough apart that none of them merge. That is the opposite shape from the much larger, heavily-collapsing BGL pattern in the companion investigation - see `worked_investigation.md` for those numbers, not repeated here since they are a different dataset's evidence. `count_by(team)` attributes 100% of these 8 entries `[E-1086]` to the Compute team via the (fictional) ownership table `[E-1086]`. For scale `[E-1087]`: this failure's 8 entries `[E-1082]` are a small fraction of nova-compute's 184 total ERROR/CRITICAL-level entries in the whole corpus (97.87% of all OpenStack errors are on nova-compute, vs 2.13% on nova-api, 0% on nova-scheduler) `[E-1087]`.
- **Hypotheses.** A roughly-even multi-hour spacing over more than a day, rather than a cluster, is consistent with this being tied to the periodic task's own schedule (it only fails when it happens to encounter a now-deleted instance on that particular pass) rather than tied to any single event upstream.
- **What to investigate first, and who owns it.** The Compute team should look at `_run_image_cache_manager_pass`'s error handling first, since - unlike a one-off - this is the single most frequent ERROR-level template in the entire corpus by raw count (tied at 8, the maximum among error templates) `[E-1089]`, and it is a real code-level gap (no per-instance isolation), not a one-time environmental issue.
- **Unknown.** Whether this also occurs on compute hosts outside this specific corpus `[E-1090]`, and whether it has any user-visible effect (the VMs whose disks were being checked do not appear to be the ones failing - no VM-side error is logged alongside these), cannot be determined from this corpus alone.

## 3. Manager

**Question:** Of the recurring problems in OpenStack, which deserves attention first right now?

**Tool calls:** `corpus_overview` `[E-1090]` → `recurring_patterns(severe)` `[E-1089]` → (this investigation's own `[E-1082, E-1085, E-1086]`)

**Answer:**

`recurring_patterns` over the whole corpus, filtered to ERROR/CRITICAL, finds only 25 distinct templates total `[E-1089]` - OpenStack's error surface here is much smaller than BGL's (which has hundreds of fault templates in the companion investigation), consistent with OpenStack being a much shorter corpus overall `[E-1090]`. Every one of the top-ranked templates by raw count is a single fragment of this *same* `InvalidDiskInfo` traceback, each appearing exactly 8 times `[E-1089]` - there is no competing, higher-frequency error pattern to rank it against here. That makes the ranking decision simple rather than requiring the episodes x components x growth formula used for BGL: this is not a close call between several candidates, it is the only recurring ERROR-level failure mode with more than a couple of occurrences in the whole dataset.

1. **nova-compute image-cache `InvalidDiskInfo` failures** - the only recurring (8 occurrences, 8 separate episodes `[E-1085]`) ERROR-level pattern in the corpus; everything else at ERROR/CRITICAL severity is a one-off `[E-1089]`. Owned by the Compute team `[E-1086]`.
2. Nothing else currently recurs at a comparable count - nova-compute's other ERROR/CRITICAL entries `[E-1087]` are spread across the other 24 templates `[E-1089]`, none of which repeats more than a few times.

**Recommended action:** the Compute team should add per-instance error isolation to `_run_image_cache_manager_pass` (the engineer-section hypothesis), since this is both the single most frequent error template in the corpus and a confirmed, reproducible code-level gap, not something that needs more data to characterise further.

## 4. What the data does and does not establish

- **Establishes:** this failure recurs 8 times `[E-1082]` over roughly a day `[E-1084]`, each a separate episode `[E-1085]`, each against a different instance's disk, confined entirely to one service (`nova-compute`) and one team (Compute) `[E-1086]`; it is the single most frequent ERROR-level template in the whole corpus `[E-1089]`.
- **Does not establish:** why each specific instance's disk was already gone, whether this has ever affected a live VM's actual availability, or any downtime/customer/cost impact - this is a short infrastructure log `[E-1090]` with no customer-facing or billing data, and none is invented here.
