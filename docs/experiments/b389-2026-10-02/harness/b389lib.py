"""Shared helpers for the B389 release-qualification harness (standard library only).

Classification follows bendlang/bend gates/safe.ts `judge` exactly (agree, u unsafe,
bend2 rejects, - out of scope, ! false reject, t timeout). Value oracles follow the
prior kvnloo/hermes-agent#389 method: emit the BendTT book with `-o`, append a
kernel claim `{main == <value> : T} = {==}` for the source value and for a different
value, and run the arm's own compiled BendTT kernel on each.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

PASS = "ALL PROOFS CHECK"
CAP_S = 30            # gates/safe_node.ts CAP default
COLD_CAP_S = 600      # first (kernel-building) verdict of an arm
OUT_KEEP = 4000


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_capped(argv, *, cwd, env, cap_s, merge=False):
    """Run argv in its own process group; SIGKILL the group at cap_s. Never raises on timeout."""
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT if merge else subprocess.PIPE,
                            start_new_session=True)
    killed = False
    try:
        out, err = proc.communicate(timeout=cap_s)
    except subprocess.TimeoutExpired:
        killed = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    ms = round((time.monotonic() - t0) * 1000, 1)
    dec = lambda b: (b or b"").decode("utf-8", errors="replace")
    return {"code": None if killed else proc.returncode, "killed": killed, "ms": ms,
            "stdout": dec(out), "stderr": dec(err) if not merge else ""}


def judge(code, ms, out: str) -> tuple[str, str]:
    """gates/safe.ts judge(), transcribed. Returns (class, reason); class in agree/rejects/u/-/!/t."""
    out = out.strip()
    if code is None or ms >= 29000:
        return "t", "timeout"
    if code == 0 and out == PASS:
        return "agree", "agree"
    if re.search(r"^Error: \d+ defs? rel(y|ies) on unsafe or foreign code", out, re.M):
        return "u", "unsafe"
    if "Sorry - " not in out:
        return "rejects", "bend2 rejects: " + " ".join(out.split("\n")[1:3])[:80]
    tt = out[out.find("BendTT: ") + 8:] if "BendTT: " in out else ""
    if tt.startswith("out of scope"):
        return "-", "out of scope"
    return "!", " | ".join(tt.split("\n")[1:3])[:300]


def kernel_path(home: Path) -> Path | None:
    bins = sorted((home / ".bend" / "bendtt").glob("*/bendtt"))
    return bins[0] if len(bins) == 1 else None


# ---- value encodings (BendTT surface syntax as emitted by Bend 2.0.34 `-o`) ----

def enc_bool(v: bool) -> str:
    return "(.True, ())" if v else "(.False, ())"


def enc_nat(n: int) -> str:
    s = "(.Zero, ())"
    for _ in range(n):
        s = "(.Succ, (" + s + ", ()))"
    return s


def enc_u32(n: int) -> str:
    bits = [(n >> i) & 1 for i in range(32)]
    s = "(.WNil, ())"
    for b in reversed(bits):
        s = "(.WCon, (" + enc_bool(bool(b)) + ", (" + s + ", ())))"
    return "(.U32, (" + s + ", ()))"


NAT_MAX = 4096


def parse_source_value(typ: str, stdout: str):
    """Parse a source run's printed value for main of BendTT type typ; None if not a single literal."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if len(lines) != 1:
        return None
    s = lines[0].strip()
    if typ == "Bool" and s in ("True{}", "False{}"):
        return s == "True{}"
    if typ == "Nat" and re.fullmatch(r"\d+n", s):
        n = int(s[:-1])
        return n if n <= NAT_MAX else None
    if typ == "U32" and re.fullmatch(r"\d+", s):
        n = int(s)
        return n if n < 2 ** 32 else None
    return None


def oracle_pair(typ: str, value):
    """(source-value term, different-value term) for the claim main == term."""
    if typ == "Bool":
        return enc_bool(value), enc_bool(not value)
    if typ == "Nat":
        return enc_nat(value), enc_nat(value + 1)
    if typ == "U32":
        return enc_u32(value), enc_u32((value + 1) % 2 ** 32)
    raise ValueError(typ)


MAIN_SIG = re.compile(r"^main : (Bool|Nat|U32) =\s*$", re.M)


def book_main_type(book_text: str):
    m = MAIN_SIG.search(book_text)
    return m.group(1) if m else None


def certified_from(same: dict, other: dict) -> str:
    """Certified relation of main to the source value from the two kernel oracles."""
    if same.get("pass") and not other.get("pass"):
        return "source"        # the certified book computes the source value
    if other.get("pass") and not same.get("pass"):
        return "different"     # the certified book computes another value
    if same.get("pass") and other.get("pass"):
        return "both"
    return "neither"


def run_oracle(kernel: Path, book: Path, typ: str, term: str, tag: str, workdir: Path, env) -> dict:
    cand = workdir / f"oracle-{tag}.bendtt"
    line = f"\nb389_oracle_{tag} : {{main == {term} : {typ}}} =\n  {{==}}\n"
    cand.write_bytes(book.read_bytes() + line.encode())
    r = run_capped([str(kernel), str(cand)], cwd=workdir, env=env, cap_s=CAP_S, merge=True)
    return {"pass": (r["code"] == 0 and r["stdout"].strip() == PASS), "exit_code": r["code"],
            "killed": r["killed"], "out": r["stdout"][:600]}


def jsonl_done(path: Path) -> set:
    done = set()
    if path.exists():
        for ln in path.read_text().splitlines():
            if ln.strip():
                try:
                    done.add(json.loads(ln)["case"])
                except (ValueError, KeyError):
                    pass
    return done
