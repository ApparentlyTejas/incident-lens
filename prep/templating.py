"""Deterministic message -> template normalisation (replaces variable parts with <*>).

The raw Loghub files do not carry a template column, so we derive a simple, reproducible
one. It is a counting aid for `recurring_patterns`, NOT Loghub's labelled event templates.
"""
import re

_RULES = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<*>"),
    (re.compile(r"\b[0-9a-f]{32}\b"), "<*>"),
    (re.compile(r"\bR\d{2}(?:-M\d)?(?:-[NL]\w+)?(?:-[CIL]:J\d+-U\d+)?\b"), "<*>"),   # BGL node ids
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<*>"),                       # IPv4
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<*>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f]|\d{6,})[0-9a-f]{6,}\b"), "<*>"),   # bare hex / long ids
    (re.compile(r"\.{3,}"), "..."),
    (re.compile(r"(?<![A-Za-z])/[\w./-]+"), "<*>"),                                      # paths / urls
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<*>"),
]
_COLLAPSE = re.compile(r"(?:<\*>[ ,:;/-]*){2,}")

def template(msg: str) -> str:
    msg = msg or ""
    for rx, rep in _RULES:
        msg = rx.sub(rep, msg)
    msg = _COLLAPSE.sub("<*> ", msg)
    return re.sub(r"\s+", " ", msg).strip()[:300]
