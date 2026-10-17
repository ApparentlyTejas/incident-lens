# Comparison: a plain LLM vs. Incident Lens, same question

> Method: a fresh Claude instance with **no tool access and no access to this project's files or
> data** was asked the exact question below and told explicitly to answer only from its own
> training knowledge, then to self-assess which of its own claims were real vs. guessed. Its full,
> unedited answer is reproduced below. This is not a strawman - it is a genuinely capable model
> doing its best with no evidence, which is exactly the situation Incident Lens is built to avoid.

**Question asked to both:** "I'm looking at system logs from a Blue Gene/L (BGL) supercomputer,
covering roughly June 2005 through January 2006 (about 4.7 million log lines, RAS logging, with
node ids, components like KERNEL/APP/HARDWARE, and severity levels including
FATAL/ERROR/WARNING/INFO). Across this period, what's most likely the single most significant
recurring problem affecting the system? Roughly how often does it happen, which component is
responsible, how many nodes does it affect, and what should the operations team do about it
first?"

## What the plain LLM said

> Dominated by memory-subsystem events, specifically correctable and uncorrectable ECC/"ddr
> errors" reported through the KERNEL component... If I had to name the single most significant
> recurring problem, my best guess is... a high-frequency stream of correctable memory (DRAM/ECC)
> bit-flip errors reported by KERNEL... Rough, non-verified estimates: this kind of
> correctable-error pattern typically shows up as a background rate of maybe a few hundred to
> low-thousands of events per week, clustering heavily on a small subset of "bad" nodes/racks...
> What operations should do first: identify and isolate the specific nodes/node-cards generating
> the highest volume of correctable ECC errors...

Its own self-assessment, unprompted by us, called every specific number in that answer "a pure
guess, not derived from any real computation on this data" and admitted the named root cause
(KERNEL/memory ECC errors) was "inference/pattern-matching from general knowledge of this
dataset's reputation," not analysis of the actual file.

## What Incident Lens said (same question, real tools, real data)

From `investigation/worked_investigation.md`, every number cited to a replayable evidence id:

- The dominant recurring problem is **`ciod` job-launch failures** ("invalid or missing program image"), an **APP**-component issue, not a KERNEL/memory issue - the plain LLM named the wrong component and the wrong category of problem entirely `[E-0097]`.
- **112,584 entries, 52,763 episodes** over the full period, not "a few hundred to low-thousands per week" - the real rate is roughly two orders of magnitude higher than the LLM's guess `[E-0101]`.
- **1,024 distinct nodes** affected - essentially the whole system, not "a small subset of bad nodes/racks" `[E-0099]`.
- Still **growing** in the most recent four weeks of data, up 224 percent, not a steady background rate `[E-0109]`.
- A concrete, inspectable example: job id 65641 (`raptor`) failing identically across dozens of nodes within milliseconds `[E-0098]`, and a named team (Application Support, via the ownership
  table) to hand it to `[E-0102]`.

## Why this comparison matters

The plain LLM's answer is fluent, plausible, and specific-sounding - it even picked a real BGL
failure mode (memory/ECC errors genuinely do occur in these logs). That is precisely the danger:
**a wrong-but-confident answer and a right-and-cited answer read the same way in prose.** The only
difference is whether every claim can be replayed against a real query. Incident Lens's answer is
checkable in one command (`get_evidence("E-0099")`); the plain LLM's answer is not checkable at
all, by its own admission.

This is also why Incident Lens's four-block answer contract and evidence ledger are not
bureaucratic overhead - they are the entire difference between these two answers' trustworthiness.
