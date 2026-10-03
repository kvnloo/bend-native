#!/usr/bin/env python3
"""Direct (no plugin) Bend --verdict sweep for one release arm, gates/safe.ts style.

usage: run_direct.py --arm A --bend <bin/bend> --root <corpus root> --cases <list> --out <jsonl>
                     --work <dir> [--workers 4] [--limit-seconds N]
Per case: `bend <f> --verdict` (cwd = root, 30 s cap, stdout+stderr merged); on a
"Sorry - " mismatch the safe_node.ts follow-up (`-o`, then the arm's kernel on the book);
safe.ts classification; then, for a certified file (class agree) or any b389cases/ file,
the value oracle (source run, emitted book, two kernel claims). Resumable: cases already in
--out are skipped; --limit-seconds stops scheduling new cases (keeps shared-lock blocks short).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b389lib as L  # noqa: E402


def arm_env(home: Path, lib: Path) -> dict:
    return {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": os.environ["TMPDIR"],
            "LANG": "C.UTF-8", "BEND_NO_TELEMETRY": "1", "BEND_LIB": str(lib),
            "BEND_HUB": "http://127.0.0.1:0"}


def identity(bend: Path, env, home: Path) -> dict:
    root = bend.resolve().parent.parent
    ver = L.run_capped([str(bend), "version"], cwd=home, env=env, cap_s=10)
    k = L.kernel_path(home)
    return {"bend_sha256": L.sha256_file(bend), "base_bend_sha256": L.sha256_file(root / "bend2/base.bend"),
            "bendtt_lean_sha256": L.sha256_file(root / "bend2/bendtt.lean"),
            "version_output": ver["stdout"].strip(), "kernel_path_key": k.parent.name if k else None,
            "kernel_sha256": L.sha256_file(k) if k else None}


def one(case: str, bend: Path, root: Path, env, work: Path, kernel: Path | None, cap: int) -> dict:
    tmp = work / "cases" / case.replace("/", "__")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    rec = {"case": case}
    v = L.run_capped([str(bend), case, "--verdict"], cwd=root, env=env, cap_s=cap, merge=True)
    out = v["stdout"]
    why = None
    book = tmp / "book.bendtt"
    if "Sorry - " in out:
        o = L.run_capped([str(bend), case, "-o", str(book)], cwd=root, env=env, cap_s=20)
        oos = o["stderr"].strip()
        if oos:
            why = oos
        elif kernel is not None and book.exists():
            kr = L.run_capped([str(kernel), str(book)], cwd=tmp, env=env, cap_s=20, merge=True)
            why = "BendTT: " + kr["stdout"].strip()
        else:
            why = "BendTT: (no book)"
        out = out + why + "\n"
    cls, reason = L.judge(v["code"], v["ms"], out)
    rec.update({"verdict_code": v["code"], "verdict_killed": v["killed"], "verdict_ms": v["ms"],
                "verdict_out": out[:L.OUT_KEEP], "class": cls, "reason": reason})
    special = case.startswith("b389cases/")
    if kernel is not None and (cls == "agree" or special):
        rec["oracle"] = value_oracle(case, bend, root, env, tmp, kernel, book)
    shutil.rmtree(tmp, ignore_errors=True)
    return rec


def value_oracle(case, bend, root, env, tmp, kernel, book) -> dict:
    o = {}
    e = L.run_capped([str(bend), case, "-o", str(book)], cwd=root, env=env, cap_s=L.CAP_S)
    o["emit_code"], o["emit_stderr"] = e["code"], e["stderr"][:600]
    if e["code"] != 0 or not book.exists():
        o["status"] = "no_book"
        return o
    o["book_sha256"] = L.sha256_file(book)
    typ = L.book_main_type(book.read_text(errors="replace"))
    o["main_type"] = typ
    if typ is None:
        o["status"] = "no_main_of_supported_type"
        return o
    s = L.run_capped([str(bend), case], cwd=root, env=env, cap_s=L.CAP_S)
    o["source_code"], o["source_stdout"] = s["code"], s["stdout"][:600]
    o["source_stderr"] = s["stderr"][:600]
    val = L.parse_source_value(typ, s["stdout"]) if s["code"] == 0 else None
    if val is None:
        o["status"] = "source_value_unparsed"
        return o
    o["source_value"] = val
    same_t, other_t = L.oracle_pair(typ, val)
    same = L.run_oracle(kernel, book, typ, same_t, "same", tmp, env)
    other = L.run_oracle(kernel, book, typ, other_t, "other", tmp, env)
    o.update({"oracle_same": same, "oracle_other": other, "certified": L.certified_from(same, other),
              "status": "evaluated"})
    return o


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--bend", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit-seconds", type=float, default=0)
    ap.add_argument("--bootstrap-case", default="b389cases/bootstrap/boot.bend")
    a = ap.parse_args()
    home, lib = a.work / "home", a.work / "bend-lib"
    home.mkdir(parents=True, exist_ok=True)
    lib.mkdir(parents=True, exist_ok=True)
    env = arm_env(home, lib)
    meta = a.out.with_suffix(".meta.jsonl")
    started = time.monotonic()
    if L.kernel_path(home) is None:
        boot = one(a.bootstrap_case, a.bend, a.root, env, a.work, None, L.COLD_CAP_S)
        boot["phase"] = "bootstrap"
        with meta.open("a") as fh:
            fh.write(json.dumps(boot) + "\n")
    kernel = L.kernel_path(home)
    ident0 = identity(a.bend, env, home)
    with meta.open("a") as fh:
        fh.write(json.dumps({"event": "start", "arm": a.arm, "identity": ident0,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    if kernel is None:
        print("no kernel after bootstrap", file=sys.stderr)
        return 3
    cases = [c for c in a.cases.read_text().split("\n") if c.strip()]
    done = L.jsonl_done(a.out)
    todo = [c for c in cases if c not in done]
    lock = threading.Lock()
    stop = threading.Event()
    n_done = 0

    def task(case):
        nonlocal n_done
        if stop.is_set():
            return
        if a.limit_seconds and time.monotonic() - started > a.limit_seconds:
            stop.set()
            return
        try:
            rec = one(case, a.bend, a.root, env, a.work, kernel, L.CAP_S)
        except Exception as exc:  # recorded, never dropped
            rec = {"case": case, "class": "harness_error", "reason": f"{type(exc).__name__}: {exc}"}
        rec["arm"] = a.arm
        with lock:
            with a.out.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            n_done += 1

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(task, todo))
    ident1 = identity(a.bend, env, home)
    remaining = len([c for c in cases if c not in L.jsonl_done(a.out)])
    with meta.open("a") as fh:
        fh.write(json.dumps({"event": "end", "arm": a.arm, "identity": ident1, "done_this_block": n_done,
                             "remaining": remaining, "kernel_unchanged": ident1 == ident0,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    print(json.dumps({"arm": a.arm, "done_this_block": n_done, "remaining": remaining,
                      "kernel_unchanged": ident1 == ident0}))
    return 0 if ident1 == ident0 else 4


if __name__ == "__main__":
    raise SystemExit(main())
