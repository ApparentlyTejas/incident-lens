#!/usr/bin/env python3
"""Answer checker. For an answer (file or stdin):
  1. every cited evidence id [E-0007] must exist in the ledger,
  2. every number written in a sentence that cites an id must appear in that ledger entry,
  3. numbers in sentences that cite nothing are flagged (dates/ids/years are ignored).
Exit code 1 if anything fails.   Usage: python eval/verify_answer.py answer.md"""
import re, sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from server import ledger

CITE = re.compile(r"\[(E-\d{4}(?:\s*,\s*E-\d{4})*)\]")
NUM = re.compile(r"(?<![\w.-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s?%)?(?![\w-])")

def numbers_in(text):
    text = re.sub(r"\d{4}-\d{2}-\d{2}[ T]?\d{0,2}:?\d{0,2}:?\d{0,2}[\d.]*", " ", text)      # timestamps
    text = re.sub(r"\bE-\d{4}\b|\bR\d\d[\w:-]*|\b[0-9a-f]{8}-[0-9a-f-]{27}\b", " ", text)       # ids, nodes, uuids
    out = []
    for m in NUM.finditer(text):
        raw = m.group(1).replace(",", ""); out.append(raw)
    return out

def entry_numbers(entry):
    blob = json.dumps(entry, default=str)
    nums = set(re.findall(r"-?\d+(?:\.\d+)?", blob))
    extra = set()
    for n in list(nums):                       # allow rounding of long decimals and ints stored as x.0
        try: f = float(n)
        except ValueError: continue
        extra |= {str(int(f)) if f == int(f) else n, f"{f:.1f}", f"{f:.2f}", str(round(f))}
    return nums | extra

def verify(answer):
    problems, checked = [], 0
    for sent in re.split(r"(?<=[.!?\n])\s+", answer):
        ids = [i.strip() for g in CITE.findall(sent) for i in g.split(",")]
        nums = [n for n in numbers_in(sent) if not (len(n) == 4 and n.startswith(("19", "20")))]
        if not ids:
            if nums and len(sent) > 25: problems.append(("UNCITED_NUMBER", sent.strip()[:140], nums))
            continue
        pool, missing_ids = set(), []
        for i in ids:
            e = ledger.get(i)
            if e is None: missing_ids.append(i)
            else: pool |= entry_numbers(e["entry"])
        if missing_ids: problems.append(("UNKNOWN_EVIDENCE_ID", sent.strip()[:140], missing_ids))
        for n in nums:
            checked += 1
            if n not in pool and n.lstrip("0") not in pool: problems.append(("NUMBER_NOT_IN_EVIDENCE", sent.strip()[:140], n))
    return checked, problems

if __name__ == "__main__":
    text = pathlib.Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    checked, problems = verify(text)
    for kind, sent, detail in problems: print(f"[{kind}] {detail}  <- {sent}")
    print(f"checked {checked} cited numbers; {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
