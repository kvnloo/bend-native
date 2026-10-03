#!/usr/bin/env python3
"""Collect the AODL lane runs into raw/ with local paths redacted (harness glue, stdlib only).

usage: collect.py --runs <runs dir> --map <redaction map JSON> --out <packet raw dir> [--originals <dir>]

The redaction map is a JSON object {"<absolute local prefix>": "${VAR}", ...} kept outside the
repository. Longest prefixes are replaced first. Every collected file's original sha256 is written
to raw/originals.sha256 so a holder of the unredacted mirror can check the redacted copies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

PREREG = Path(__file__).with_name("PREREG.json")
META = ("argv", "exit_code", "started_at", "ended_at", "net.inside", "mask.inside", "worktree-head",
        "worktree-status", "live-home-stat.before", "live-home-stat.after", "python", "home",
        "home-upper.list", "env.inside", "stderr", "stdout")
TOP = ("receipt.json", "replay-receipt.json")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redact(text: str, mapping: list[tuple[str, str]]) -> str:
    for prefix, var in mapping:
        text = text.replace(prefix, var)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--originals", type=Path, help="mirror the unredacted files here")
    args = ap.parse_args()
    mapping = sorted(json.loads(args.map.read_text()).items(), key=lambda kv: -len(kv[0]))
    runs = [r["id"] for r in json.loads(PREREG.read_text())["runs"]]
    runs += sorted(p.name for p in args.runs.glob("aodl-*") if p.name not in runs)
    if args.out.exists():
        shutil.rmtree(args.out)
    originals = []
    for run in runs:
        src = args.runs / run
        if not src.is_dir():
            continue
        names = [f"meta/{m}" for m in META] + list(TOP)
        cwd = src / "cwd"
        if cwd.is_dir():
            lines = [f"{sha(p.read_bytes())}  ./{p.relative_to(cwd).as_posix()}"
                     for p in sorted(cwd.rglob("*")) if p.is_file()]
            (src / "meta" / "cwd.sha256").write_text("\n".join(lines) + "\n")
            names.append("meta/cwd.sha256")
        for name in names:
            path = src / name
            if not path.is_file():
                continue
            data = path.read_bytes()
            originals.append(f"{sha(data)}  {run}/{name}")
            target = args.out / run / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(redact(data.decode("utf-8", "replace"), mapping))
            if args.originals:
                mirror = args.originals / run / name
                mirror.parent.mkdir(parents=True, exist_ok=True)
                mirror.write_bytes(data)
    (args.out / "originals.sha256").write_text("\n".join(originals) + "\n")
    print(f"collected {len(originals)} files from {len(runs)} runs")


if __name__ == "__main__":
    main()
