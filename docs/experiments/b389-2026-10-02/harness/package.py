#!/usr/bin/env python3
"""package.py <stage dir> <lane dir> <lock ledger> <packet dir> <scrub-tokens.json>: copy raw records into <packet>/raw, scrubbed.

Absolute local paths are replaced by stable tokens (the unscrubbed originals stay in the lane mirror).
Writes raw/*.jsonl.gz (gzip mtime 0), raw/lists/*, raw/cli/<run>/{exit_code,stdout.json}, raw/runs.jsonl
(one line per launcher run: argv, rc, start/end, masks, live-home stat unchanged) and raw/ledger-b389.jsonl.
"""
import gzip
import json
import re
import shutil
import sys
from pathlib import Path

ST, LANE, LEDGER, PKT = (Path(p) for p in sys.argv[1:5])
RAW = PKT / "raw"
# extra (prefix, token) pairs for local absolute paths come from a JSON file outside the packet
TOKENS = [(str(ST), "$STAGE"), (str(LANE), "$LANE")] + [tuple(x) for x in json.loads(Path(sys.argv[5]).read_text())]


def scrub(text: str) -> str:
    for a, b in TOKENS:
        text = text.replace(a, b)
    text = re.sub("/tmp/" + "claude" + r"[^\s\"']*", "$CLAUDE_TMP", text)
    return text


def gz(src: Path, dst: Path):
    data = scrub(src.read_text()).encode()
    with open(dst, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, filename="") as z:
            z.write(data)


if RAW.exists():
    shutil.rmtree(RAW)
(RAW / "lists").mkdir(parents=True)
for p in sorted((ST / "out").glob("*.jsonl")):
    gz(p, RAW / (p.name + ".gz"))
for p in sorted((ST / "lists").glob("*.txt")):
    (RAW / "lists" / p.name).write_text(p.read_text())
runs = []
for d in sorted((LANE / "runs").glob("b389-*")):
    m = d / "meta"
    if not m.exists():
        continue
    rd = lambda n: (m / n).read_text().strip() if (m / n).exists() else None
    # the live home directory's own mtime moves with the user's running Hermes; compare its files only
    files_only = lambda t: None if t is None else [ln for ln in t.splitlines() if not re.match(r"^[^|]*hermes-home\|", ln)]
    before, after = files_only(rd("live-home-stat.before")), files_only(rd("live-home-stat.after"))
    runs.append({"run": d.name, "pilot": d.name.startswith("b389-pilot"), "argv": scrub(rd("argv") or "").split("\n"),
                 "exit_code": rd("exit_code"), "started_at": rd("started_at"), "ended_at": rd("ended_at"),
                 "worktree_head": rd("worktree-head"), "mask_inside": rd("mask.inside"),
                 "live_home_files_unchanged": before is not None and before == after})
    if d.name.startswith("b389-cli-"):
        c = RAW / "cli" / d.name
        c.mkdir(parents=True)
        (c / "exit_code").write_text((rd("exit_code") or "") + "\n")
        (c / "stdout.json").write_text(scrub((m / "stdout").read_text()))
        if (m / "stderr").exists():
            (c / "stderr.log").write_text(scrub((m / "stderr").read_text())[-4000:])
(RAW / "runs.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in runs))
lines = [ln for ln in Path(LEDGER).read_text().splitlines() if '"lane":"B389"' in ln]
(RAW / "ledger-b389.jsonl").write_text("".join(scrub(ln) + "\n" for ln in lines))
print(json.dumps({"raw_files": len(list(RAW.rglob("*"))), "runs": len(runs), "ledger_lines": len(lines)}))
