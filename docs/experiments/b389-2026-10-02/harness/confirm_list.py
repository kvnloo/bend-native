#!/usr/bin/env python3
"""confirm_list.py <out dir>: the PREREG confirmation set from the primary pass, one case per line.

A T/X case is confirmed when its direct class differs between any two arms, is t or harness_error on any
arm, or the arm's plugin pass disagrees with direct agree (layout_na and plugin input-policy errors excluded).
"""
import json
import sys
from pathlib import Path

ARMS = ["A", "B", "C1", "C2"]
POLICY = {"unsupported_import", "unsupported_input", "invalid_input", "input_capture_failed"}
out = Path(sys.argv[1])


def load(p):
    d = {}
    for ln in p.read_text().splitlines():
        if ln.strip():
            r = json.loads(ln)
            d[r["case"]] = r
    return d


direct = {a: load(out / f"direct-{a}.jsonl") for a in ARMS}
plugin = {a: load(out / f"plugin-{a}.jsonl") for a in ARMS}
sel = []
for c in sorted(direct["A"]):
    if c.startswith("b389cases/"):
        continue
    cls = {a: direct[a][c]["class"] for a in ARMS}
    hit = len(set(cls.values())) > 1 or any(v in ("t", "harness_error") for v in cls.values())
    for a in ARMS:
        p = plugin[a].get(c, {})
        if p.get("plugin_status") != "ran" or ("verdict" not in p and p.get("code") in POLICY):
            continue
        if (p.get("verdict") == "pass") != (cls[a] == "agree"):
            hit = True
    if hit:
        sel.append(c)
print("\n".join(sel))
