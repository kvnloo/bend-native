#!/usr/bin/env python3
"""Independent check of the B390 packet. Standard library only; never imports the plugin
or harness/analyze.py (it uses harness/fixtures.py, the fixture definition itself).

  python3 verify_artifacts.py            # from the packet directory or anywhere
Exit 0 when every check passes; prints one line per check.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

PACKET = Path(__file__).resolve().parent
RAW = PACKET / "raw"
sys.path.insert(0, str(PACKET / "harness"))
import fixtures as F  # noqa: E402

OFFICIAL = {"bend": "7fafb749dd33df446a0cb5839c5694df237312a8e8048d2e9420480164ff5934",
            "base.bend": "c742fae9c49b14f0cc9128429a2c6109364c8a933a142f2c90b9f2e5fd976661",
            "bendtt.lean": "61e0d2d9f4ddd7dd8bb1497fa612fb49a7274fcac647bf70702ba22321d7c292"}
PATCHED = {"bend": "b8473e168c200d676440a4079479d16d6e8583610d3447a97c1d8be9108ad981",
           "base.bend": "c742fae9c49b14f0cc9128429a2c6109364c8a933a142f2c90b9f2e5fd976661",
           "bendtt.lean": "e15042434e73aab07ab05cea4b77b5619082c00a6d4924ea2d4a2cdec05facce"}
KERNELS = {"a7e5203de58d989b97e759c014d2347d4355ebc652d696012c7552db0716bfca": "official",
           "72e11a86f44563e9decb26fe1c586ed7d5465e98ca282ada5ae8753b1156cad5": "patched"}
PLUGIN_SHA = "e85e65e5d2e11caba8412d6dbd19aad03fd785ad"

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


PACKAGES, IDS, NAMES, PROJECTS, CLOSURE = F.build()


def manifest_digest(files):
    h = hashlib.sha256()
    for rel, data in sorted(files.items()):
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest() + b"\n")
    return h.hexdigest()


def identity(project, rebound=False):
    names = dict(CLOSURE[project]["names"])
    keys = list(CLOSURE[project]["packages"])
    if rebound:
        names, keys = {"sample@1.0.0.0": "Q_alt"}, ["Q_alt"]
    dep = {IDS[k] + "/" + rel: d for k in keys for rel, d in PACKAGES[k].items()}
    dep.update({"names/" + nv: (IDS[k] + "\n").encode() for nv, k in names.items()})
    comb = {**{"project/" + k: v for k, v in PROJECTS[project].items()},
            **{"dependencies/" + k: v for k, v in dep.items()}}
    return {"dependency_packages": {IDS[k]: hashlib.sha256(F.manifest(PACKAGES[k])).hexdigest() for k in keys},
            "dependency_names": {nv: IDS[k] for nv, k in names.items()},
            "dependency_manifest_sha256": manifest_digest(dep),
            "input_manifest_sha256": manifest_digest(comb)}


def outcome(r):
    return r["verdict"] if "verdict" in r else "error:" + str(r.get("code"))


# 1. fixture identities follow Bend's publication rule (recomputed here from bytes)
for key, files in PACKAGES.items():
    text = "".join(hashlib.sha256(files[p]).hexdigest() + " " + p + "\n" for p in sorted(files))
    check(f"fixture id {key}", "0x" + hashlib.sha256(text.encode()).hexdigest()[:32] == IDS[key], IDS[key])

# 2. load rows and receipts
rows = []
for path in sorted(RAW.glob("*/rows.jsonl")) + sorted(RAW.glob("*/worker*/rows.jsonl")):
    for line in path.read_text().splitlines():
        row = json.loads(line)
        row["_r"] = json.loads((path.parent / row["receipt_file"]).read_text())
        row["_dir"] = path.parent.relative_to(RAW).as_posix()
        rows.append(row)
check("rows loaded (coverage is checked per cell below)", len(rows) > 0, str(len(rows)))
check("row outcome == receipt outcome", all(r["outcome"] == outcome(r["_r"]) for r in rows))
check("row success == receipt success", all(r["success"] == bool(r["_r"].get("success")) for r in rows))
plugin_receipts = [r["_r"] for r in rows if "verdict" in r["_r"] and not r["_r"].get("raw_bend")]
check("every plugin verdict receipt keeps emitted-book scope",
      all(x.get("verification_scope") == "bend-emitted-book" and x.get("source_semantics_attested") is False
          for x in plugin_receipts), str(len(plugin_receipts)))
check("success implies verdict pass",
      all(x["verdict"] == "pass" for x in plugin_receipts if x.get("success")))

# 3. A1: every successful plugin receipt names exactly the recorded closure
FORGE = {"O5_mutated_forging": True, "F5_mutated_forging_offline": True}
REBOUND = {"O8d_binding_rebound_fresh_verify", "O13a_replay_hub_rebound_refetch"}
succ = [r for r in rows if r["success"] and r["step"] != "raw_bend_control" and r["kind"] in ("H", "N")]
bad = []
for r in succ:
    project = {"H": "HF", "N": "NF"}[r["kind"]] if r["cell"] in FORGE else r["kind"]
    exp = identity(project, r["cell"] in REBOUND and r["step"] in ("verify", "fresh_verify_control"))
    if any(r["_r"].get(k) != v for k, v in exp.items()):
        bad.append((r["cell"], r["kind"], r["run"], r["step"]))
cli_runs = sorted((RAW / "cli").glob("cli-*"))
cli = []
for run in cli_runs:
    meta = dict(l.split("=", 1) for l in (run / "cli.meta").read_text().split())
    out = json.loads((run / "cli.stdout").read_text())
    _, mode, kind, _ = run.name.split("-")
    cli.append((run.name, mode, kind, int(meta["cli_rc"]), out,
                [l.split(":")[0].strip() for l in (run / "netns.dev").read_text().splitlines()[2:] if l.strip()]))
    if out.get("success") and any(out.get(k) != v for k, v in identity(kind).items()):
        bad.append((run.name,))
check("A1 successful receipts carry exact immutable identities", succ and not bad,
      f"{len(succ)} matrix + {sum(1 for c in cli if c[4].get('success'))} CLI successes, mismatches {bad[:3]}")

# 4. A2: no replay follows a different package
a2 = [r for r in rows if r["step"] == "replay" and r["cell"] in {
    "O13a_replay_hub_rebound_refetch", "O13b_replay_local_rebound", "O14_replay_race_injected",
    "F8_replay_offline_local_rebound", "E2_patched_replay_official_receipt"}]
check("A2 zero successes when the resolved package/installation differs", a2 and not any(r["success"] for r in a2),
      str(Counter(r["cell"] + "|" + r["kind"] + "|" + r["outcome"] for r in a2)))
check("A2 rebound replays are receipt_stale",
      all(r["outcome"] == "error:receipt_stale" for r in a2 if r["cell"].startswith(("O13a", "O13b", "F8", "E2"))))
check("A2 rebind after precheck is replay_mismatch",
      all(r["outcome"] == "replay_mismatch" and "input_manifest_sha256" in (r["_r"].get("replay_differences") or [])
          for r in a2 if r["cell"].startswith("O14") and r["kind"] == "N"))

# 5. A3: mutation never yields success; raw Bend accepts the same mutated caches
a3_cells = ("O4_", "O5_", "O6_", "O7_", "O8b_", "O11_", "O15_", "O16_", "F4_", "F5_", "F9_")
a3 = [r for r in rows if r["cell"].startswith(a3_cells) and r["step"] in ("verify", "replay")]
ctrl = [r for r in rows if r["step"] == "raw_bend_control"]
check("A3 zero plugin successes on mutated caches", a3 and not any(r["success"] for r in a3), f"{len(a3)} rows")
check("A3 raw Bend controls pass on the same mutated caches", ctrl and all(r["success"] for r in ctrl), f"{len(ctrl)} controls")
dv = [r for r in rows if r["cell"].startswith(("O15_", "O16_", "F9_"))]
check("A3 during-verdict mutations landed while the child ran", dv and all(r.get("landed") for r in dv),
      f"{sum(bool(r.get('landed')) for r in dv)}/{len(dv)}")
check("A3 during-verdict results are unstable", all(r["outcome"] == "unstable" for r in dv))

# 6. A4: offline replay of the online-captured receipt
f6 = [r for r in rows if r["cell"] == "F6_replay_offline"]
check("A4 F6 offline replays pass with replay_match", len(f6) >= 10 and all(r["success"] and r["replay_match"] for r in f6), f"{len(f6)}")
cr = [c for c in cli if c[1] == "replay"]
check("A4 CLI offline replays exit 0 with replay_match", len(cr) >= 10 and all(c[3] == 0 and c[4].get("replay_match") is True for c in cr), f"{len(cr)}")
cv = [c for c in cli if c[1] == "verify"]
check("CLI offline verifies exit 0 with pass", len(cv) >= 10 and all(c[3] == 0 and c[4].get("verdict") == "pass" for c in cv), f"{len(cv)}")

# 7. A5: no network after capture
online = [r for r in rows if r["_dir"] == "online" and "hub_requests" in r]
check("A5 online plugin calls made zero hub requests", online and all(r["hub_requests"] == 0 for r in online), f"{len(online)} calls")
fetch_rows = [r for r in rows if r.get("fetch_hub_requests") is not None]
check("A5 control: Bend's own fetch did contact the hub", fetch_rows and all(r["fetch_hub_requests"] > 0 for r in fetch_rows))
for phase in ("offline", "workers", "patched"):
    o = json.loads((RAW / phase / "offline-outer.json").read_text())
    check(f"A5 {phase}: hub log unchanged during the namespaced run", o["hub_requests_during_inner"] == 0 and o["inner_exit"] == 0)
for phase in ("offline", "patched"):
    probes = [json.loads(l) for l in (RAW / phase / "notes.jsonl").read_text().splitlines() if '"netprobe"' in l]
    check(f"A5 {phase}: only lo and the hub refused",
          probes and all([x.split(":")[0].strip() for x in p["proc_net_dev"] if x.strip()] == ["lo"]
                         and "Refused" in p["hub_connect"] for p in probes))
check("A5 CLI runs had only lo", cli and all(c[5] == ["lo"] for c in cli))
f2 = [r for r in rows if r["cell"] == "F2_cold_fetch_offline"]
check("A5 control: Bend's fetch fails offline", f2 and all(r["fetch_rc"] != 0 for r in f2))

# 8. coverage and denominators
cells = Counter((r["cell"], r["kind"]) for r in rows if r["kind"] in ("H", "N") and not r["step"].startswith("attempt") and r["step"] not in ("raw_bend_control", "verify_before_mutation", "fresh_verify_control"))
small = {k: v for k, v in cells.items() if v < 5}
check("every cell x kind has >= 5 runs", not small, str(small))
w = [r for r in rows if r["cell"].startswith("W1")]
check("W1 3 processes x 5 x 2 kinds all pass", len(w) == 30 and all(r["success"] for r in w), str(len(w)))
wo = json.loads((RAW / "workers" / "workers-outer.json").read_text())
check("W1 shared cache unchanged, workers exit 0", wo["shared_cache_unchanged"] and wo["worker_exit_codes"] == [0, 0, 0])
dev = [r for r in rows if not r["as_expected"]]
check("deviation rows are listed in summary.json", len(json.loads((PACKET / "summary.json").read_text())["deviations"]) >= len(dev), f"{len(dev)} row deviations")

# 9. identities
kernels = Counter(r["_r"].get("kernel_sha256_after") for r in rows if r["_r"].get("kernel_sha256_after"))
check("kernel hashes are the expected official/patched builds", set(kernels) <= set(KERNELS), str({KERNELS.get(k, k): v for k, v in kernels.items()}))
runtimes = {json.dumps(x.get("runtime_identity"), sort_keys=True) for x in plugin_receipts}
check("runtime identities are the pinned official/patched installs",
      runtimes <= {json.dumps(OFFICIAL, sort_keys=True), json.dumps(PATCHED, sort_keys=True)}, str(len(runtimes)))
idents = [json.loads(l) for p in RAW.glob("*/notes.jsonl") for l in p.read_text().splitlines() if '"identity"' in l]
check("installed plugin HEAD is the tested SHA", idents and all(i["plugin_head"] == PLUGIN_SHA for i in idents), str(len(idents)))

# 10. hygiene and pre-registration order
import re  # noqa: E402
blob = "\n".join(p.read_text(errors="replace") for p in RAW.rglob("*") if p.is_file())
host_path = re.compile(r"(?<![\w$}.-])/(mnt|home|workspace|root|srv|media)/")
extra = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--forbid=")]
check("no unscrubbed absolute host paths in raw/ (plus any --forbid=STRING)",
      not host_path.search(blob) and not any(s in blob for s in extra), f"{len(extra)} extra strings")
try:
    root = subprocess.run(["git", "-C", str(PACKET), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
    rel = PACKET.relative_to(root).as_posix()
    first = lambda p: subprocess.run(["git", "-C", root, "log", "--reverse", "--format=%H %ct", "--", p], capture_output=True, text=True).stdout.split("\n")[0].split()
    pre, raw_c = first(rel + "/PREREG.json"), first(rel + "/raw")
    if pre and raw_c:
        anc = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", pre[0], raw_c[0]]).returncode == 0
        check("PREREG.json committed before raw results", anc and pre[0] != raw_c[0], f"{pre[0][:8]} -> {raw_c[0][:8]}")
    else:
        print("SKIP PREREG order (packet not committed yet)")
except (subprocess.CalledProcessError, FileNotFoundError):
    print("SKIP PREREG order (no git)")

print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
