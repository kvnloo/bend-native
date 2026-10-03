"""Independent Bend oracle: a fresh `bend ./PROOF.bend --verdict` on a private copy of the final project.

Runs outside Hermes (no plugin code), under hostless with env -i. Never writes into the project.
usage: oracle_bend.py <bend executable> <project dir> <expected LAWS.bend sha256> <out.json>
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

bend, project, laws_expected, out = sys.argv[1:5]
project = Path(project)
def sha(p):
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None
files = {p.relative_to(project).as_posix(): sha(p) for p in sorted(project.rglob('*.bend')) if p.is_file()}
laws = files.get('LAWS.bend')
rec = {"schema": "bend_e2e.oracle.v1", "files": files, "laws_sha256": laws,
       "laws_unchanged": laws == laws_expected, "bend_sha256": sha(Path(bend))}
home = os.environ["ORACLE_HOME"]
kernel_src = Path(bend).resolve().parent.parent / "bend2" / "bendtt.lean"
src_key = hashlib.sha256(kernel_src.read_bytes()).hexdigest()[:16]
kernel = Path(home) / ".bend" / "bendtt" / src_key / "bendtt"
rec["kernel_sha256_before"] = sha(kernel)
with tempfile.TemporaryDirectory(prefix="oracle-", dir=os.environ.get("TMPDIR")) as tmp:
    snap = Path(tmp) / "project"
    shutil.copytree(project, snap, symlinks=True, ignore=shutil.ignore_patterns("*.json", "*.log"))
    env = {"PATH": os.environ["PATH"], "HOME": home, "TMPDIR": tmp, "LANG": "C.UTF-8",
           "BEND_NO_TELEMETRY": "1", "BEND_HUB": "http://127.0.0.1:0", "BEND_LIB": str(Path(tmp) / "lib")}
    t0 = time.monotonic()
    try:
        p = subprocess.run([bend, "./PROOF.bend", "--verdict"], cwd=snap, env=env, capture_output=True,
                           text=True, timeout=300)
        rec.update(rc=p.returncode, stdout=p.stdout[-4000:], stderr=p.stderr[-4000:], timed_out=False)
    except subprocess.TimeoutExpired as e:
        rec.update(rc=None, stdout="", stderr="timeout", timed_out=True)
    rec["wall_s_incidental"] = round(time.monotonic() - t0, 3)
rec["kernel_sha256_after"] = sha(kernel)
rec["verdict"] = "pass" if rec.get("rc") == 0 and rec.get("stdout", "").strip() == "ALL PROOFS CHECK" else "fail"
Path(out).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
print(json.dumps({k: rec[k] for k in ("verdict", "rc", "laws_unchanged")}))
