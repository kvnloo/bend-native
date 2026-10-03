#!/usr/bin/env python3
"""Blind check of the B389 packet: run from the packet directory inside a git checkout.

1. summary.json equals a fresh analyze.py run over raw/ (byte-for-byte JSON).
2. PREREG.json was committed before raw/ and is unchanged since that commit.
3. Every arm's recorded binary and kernel identities match PREREG.json.
4. Record counts: primary pass covers every listed case per arm and mode; special reps have 15 cases.
5. No absolute local path in any packet file.
Exit 0 only if every check passes.
"""
from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f" :: {detail}" if detail and not cond else ""))
    ok = ok and bool(cond)


def git(*args):
    return subprocess.run(["git", "-C", str(HERE), *args], capture_output=True, text=True).stdout.strip()


tmp = HERE / "raw" / ".summary.recomputed.json"
subprocess.run([sys.executable, str(HERE / "analyze.py"), str(HERE / "raw"), "--out", str(tmp)], capture_output=True)
committed = json.loads((HERE / "summary.json").read_text())
recomputed = json.loads(tmp.read_text())
tmp.unlink()
check("summary.json == analyze.py(raw/)", committed == recomputed)

rel = HERE.relative_to(Path(git("rev-parse", "--show-toplevel")))
prereg_commit = git("log", "--diff-filter=A", "--format=%H", "--", f"{rel}/PREREG.json").splitlines()[-1:]
raw_commit = git("log", "--diff-filter=A", "--format=%H", "--", f"{rel}/raw").splitlines()[-1:]
check("PREREG.json committed", bool(prereg_commit))
check("raw/ committed", bool(raw_commit))
if prereg_commit and raw_commit:
    anc = subprocess.run(["git", "-C", str(HERE), "merge-base", "--is-ancestor", prereg_commit[0], raw_commit[0]]).returncode == 0
    check("PREREG commit is an ancestor of the raw/ commit and differs from it", anc and prereg_commit[0] != raw_commit[0])
    changed = git("diff", "--name-only", prereg_commit[0], "HEAD", "--", f"{rel}/PREREG.json")
    check("PREREG.json unchanged since its commit", changed == "", changed)

prereg = json.loads((HERE / "PREREG.json").read_text())
for arm in ["A", "B", "C1", "C2"]:
    exp_bin = prereg["arms"][arm]["bin_sha256"]
    exp_k = prereg["arms"][arm]["expected_kernel_sha256"].split()[0]
    a = committed["arms"][arm]["identity"]
    check(f"arm {arm} binary identity", a["direct_bin_sha256"] == [exp_bin], a["direct_bin_sha256"])
    check(f"arm {arm} kernel identity (direct and plugin)", a["direct_kernel_sha256"] == [exp_k] and a["plugin_kernel_sha256"] == [exp_k],
          f'{a["direct_kernel_sha256"]} {a["plugin_kernel_sha256"]}')


def count(name):
    p = HERE / "raw" / (name + ".jsonl.gz")
    if not p.exists():
        return -1
    return len({json.loads(l)["case"] for l in gzip.decompress(p.read_bytes()).decode().splitlines() if l.strip()})


lists = HERE / "raw" / "lists"
n_primary = len([l for l in (lists / "primary.txt").read_text().splitlines() if l.strip()])
for arm in ["A", "B", "C1", "C2"]:
    for mode in ["direct", "plugin"]:
        check(f"{mode}-{arm} primary covers {n_primary} cases", count(f"{mode}-{arm}") == n_primary, count(f"{mode}-{arm}"))
        for rep in ["r2", "r3"]:
            check(f"{mode}-{arm}-special-{rep} covers 15 cases", count(f"{mode}-{arm}-special-{rep}") == 15)
conf = lists / "confirm.txt"
if conf.exists():
    n_conf = len([l for l in conf.read_text().splitlines() if l.strip()])
    for arm in ["A", "B", "C1", "C2"]:
        for rep in ["r2", "r3"]:
            check(f"direct-{arm}-confirm-{rep} covers {n_conf} cases", count(f"direct-{arm}-confirm-{rep}") == n_conf)

bad = []
pat = re.compile(rb"/(mnt|home|workspace)/[A-Za-z0-9_.-]+|/tmp/" + b"claude")
for p in sorted(HERE.rglob("*")):
    if p.is_file():
        data = p.read_bytes()
        if p.suffix == ".gz":
            data = gzip.decompress(data)
        if pat.search(data):
            bad.append(str(p.relative_to(HERE)))
check("no absolute local paths in packet files", not bad, ", ".join(bad[:5]))
print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
