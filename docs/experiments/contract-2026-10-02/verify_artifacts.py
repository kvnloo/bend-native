#!/usr/bin/env python3
"""Verify the contract-lane packet from its committed files only.

usage: python3 verify_artifacts.py            (run from anywhere; prints RESULT PASS|FAIL)
Checks:
  1. SHA256SUMS covers every packet file and every digest matches;
  2. analyze.py re-run over raw/ reproduces summary.json exactly;
  3. PREREG.json was committed before the first measured run started (git history + raw ledgers);
  4. every planned run has a raw record; every measured run's record carries the pinned plugin and Hermes heads;
  5. no local absolute path, host name or user name leaked into the packet;
  6. in-process records (raw/inproc) are present for every section the README reports;
  7. the bend-task oracle key comes from a direct official Bend run on the committed fixture (D7).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_SHA = "e85e65e5d2e11caba8412d6dbd19aad03fd785ad"
HERMES_SHA = "ad31bbf079f0ee559ce1b61c5f85178c4c3d9396"
SECTIONS = ("h2", "h3", "h3slow", "h4", "h4x", "h5", "h6", "h6cost")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + str(detail) if detail else ''}")


# 1. checksums
sums = {}
for line in (HERE / "SHA256SUMS").read_text().splitlines():
    digest, name = line.split("  ", 1)
    sums[name] = digest
files = sorted(str(p.relative_to(HERE)) for p in HERE.rglob("*")
               if p.is_file() and p.name != "SHA256SUMS" and "__pycache__" not in p.parts)
bad = [f for f in files if sums.get(f) != hashlib.sha256((HERE / f).read_bytes()).hexdigest()]
check("checksums cover all files", set(files) == set(sums), sorted(set(files) ^ set(sums))[:5])
check("checksums match", not bad, bad[:5])

# 2. analysis reproduces summary.json
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "summary.json"
    proc = subprocess.run([sys.executable, "-B", str(HERE / "harness" / "analyze.py"), str(HERE), "--out", str(out)],
                          capture_output=True, text=True)
    same = proc.returncode == 0 and json.loads(out.read_text()) == json.loads((HERE / "summary.json").read_text())
    check("analyze.py reproduces summary.json", same, proc.stderr[-300:] if proc.returncode else "")

# 3. PREREG before measured runs
try:
    log = subprocess.run(["git", "log", "--diff-filter=A", "--format=%cI", "--", "PREREG.json"], cwd=HERE,
                         capture_output=True, text=True, check=True).stdout.split()
    prereg_at = datetime.fromisoformat(log[-1]).astimezone(timezone.utc)
    starts = []
    for led in (HERE / "raw" / "runs").glob("m*/ledger.json"):
        starts.append(datetime.fromtimestamp(json.loads(led.read_text())["started"], timezone.utc))
    check("PREREG committed before first measured run", starts and prereg_at < min(starts),
          f"prereg {prereg_at.isoformat()} first run {min(starts).isoformat() if starts else None}")
except (subprocess.CalledProcessError, IndexError, FileNotFoundError) as exc:
    check("PREREG committed before first measured run", False, f"git history unavailable: {exc}")

# 4. coverage and identities
plan = json.loads((HERE / "plan.json").read_text())
missing = [r["run_id"] for r in plan["runs"] if not (HERE / "raw" / "runs" / r["run_id"] / "run.json").exists()]
check("every planned run has a raw record", not missing, missing[:5])
wrong = []
for r in plan["runs"]:
    meta = HERE / "raw" / "runs" / r["run_id"] / "meta"
    if not meta.exists():
        continue
    heads = ((meta / "plugin-head").read_text().strip() if (meta / "plugin-head").exists() else None,
             (meta / "worktree-head").read_text().strip() if (meta / "worktree-head").exists() else None)
    status = ((meta / "plugin-status").read_text().strip() if (meta / "plugin-status").exists() else "?",
              (meta / "worktree-status").read_text().strip() if (meta / "worktree-status").exists() else "?")
    if heads != (PLUGIN_SHA, HERMES_SHA) or any(status):
        wrong.append((r["run_id"], heads, status))
check("plugin and Hermes heads pinned and clean in every run", not wrong, wrong[:3])

# 5. leak scan
# generic local-path prefixes (assembled so this file does not match itself) plus the host name by digest only
pattern = re.compile(b"(" + b"|".join([b"/m" + b"nt/", b"/ho" + b"me/[a-z]", b"/work" + b"space/", b"/tmp/cl" + b"aude"]) + b")")
HOST_SHA256 = "c76d9da671ff244fa274ad83621cc1ae1c3a2a78b5baa590ad5ab0b5347ea1dd"
leaks = []
for f in files:
    data = (HERE / f).read_bytes()
    if f.startswith("raw/bodies/"):
        import gzip
        data = gzip.decompress(data)
    words = set(re.findall(rb"[A-Za-z0-9_-]{3,32}", data))
    if pattern.search(data) or any(hashlib.sha256(w).hexdigest() == HOST_SHA256 for w in words):
        leaks.append(f)
check("no local paths or host name in the packet", not leaks, leaks[:5])

# 6. in-process records
present = {p.stem for p in (HERE / "raw" / "inproc").glob("*.json")}
check("in-process records present", all(s in present for s in SECTIONS), sorted(set(SECTIONS) - present))

# 7. bend oracle key established without the plugin (DEVIATIONS D7)
key = json.loads((HERE / "raw" / "oracle_key" / "bend-verdict.json").read_text())
fixture = {n: hashlib.sha256((HERE / "fixtures" / "bend" / n).read_bytes()).hexdigest() for n in ("LAWS.bend", "PROOF.bend")}
check("bend oracle key: direct official Bend verdict on the committed fixture is pass",
      key["exit_code"] == 0 and key["stdout"].strip() == "ALL PROOFS CHECK" and key["inputs_sha256"] == fixture
      and key["kernel_built_sha256"].startswith("a7e5203d"), key["stdout"].strip())

ok = all(r[1] for r in results)
print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
