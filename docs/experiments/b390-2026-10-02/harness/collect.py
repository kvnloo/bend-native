#!/usr/bin/env python3
"""Copy lane outputs into the packet's raw/ with host paths replaced by variables.

collect.py --raw DIR [--phase NAME=SRC]... [--cli NAME=RUNDIR]... [--meta NAME=RUNDIR]...
           [--scrub PREFIX=TOKEN]... [--forbid STRING]...
Text files are rewritten with every PREFIX replaced by its TOKEN (longest prefix first);
the copy fails if any --forbid string survives. Binary files are not expected.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

CLI_FILES = ("cli.meta", "cli.stdout", "cli.stderr", "netns.dev", "receipt.json", "config-set.out")
META_FILES = ("argv", "exit_code", "mask.inside", "live-home-stat.before", "live-home-stat.after",
              "worktree-head", "worktree-status", "started_at", "ended_at", "stderr", "stdout", "python")


def scrub_text(text, pairs):
    for prefix, token in pairs:
        text = text.replace(prefix, token)
    return text


def copy_file(src: Path, dst: Path, pairs, forbid, report):
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"binary file not expected: {src}")
    text = scrub_text(text, pairs)
    for word in forbid:
        if word in text:
            report.append(f"{dst}: contains forbidden string")
    dst.write_text(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--phase", action="append", default=[])
    ap.add_argument("--cli", action="append", default=[])
    ap.add_argument("--meta", action="append", default=[])
    ap.add_argument("--scrub", action="append", default=[])
    ap.add_argument("--forbid", action="append", default=[])
    args = ap.parse_args()
    raw = Path(args.raw)
    pairs = sorted((tuple(s.split("=", 1)) for s in args.scrub), key=lambda p: -len(p[0]))
    report = []
    for spec in args.phase:
        name, src = spec.split("=", 1)
        for path in sorted(Path(src).rglob("*")):
            if path.is_file():
                copy_file(path, raw / name / path.relative_to(src), pairs, args.forbid, report)
    for spec in args.cli:
        name, run = spec.split("=", 1)
        for fname in CLI_FILES:
            if (Path(run) / "cwd" / fname).exists():
                copy_file(Path(run) / "cwd" / fname, raw / "cli" / name / fname, pairs, args.forbid, report)
    for spec in args.meta:
        name, run = spec.split("=", 1)
        for fname in META_FILES:
            if (Path(run) / "meta" / fname).exists():
                copy_file(Path(run) / "meta" / fname, raw / "launcher" / name / fname, pairs, args.forbid, report)
    if report:
        print("\n".join(report), file=sys.stderr)
        raise SystemExit(1)
    print(f"collected into {raw}")


if __name__ == "__main__":
    main()
