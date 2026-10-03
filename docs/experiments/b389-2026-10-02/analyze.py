#!/usr/bin/env python3
"""Reduce the B389 raw records to summary.json per PREREG.json (standard library only).

usage: analyze.py <raw dir> [--out summary.json]
raw dir layout: direct-<arm>[-special-r2|-special-r3|-confirm-r2|-confirm-r3].jsonl,
plugin-<arm>[-special-r2|-special-r3].jsonl, *.meta.jsonl, cli/<run-id>/{exit_code,stdout.json}.
"""
from __future__ import annotations

import collections
import gzip  # noqa: F401  (load() reads *.jsonl.gz)
import json
import sys
from pathlib import Path

ARMS = ["A", "B", "C1", "C2"]
EXPECTED_KERNEL = {"A": "a7e5203de58d989b97e759c014d2347d4355ebc652d696012c7552db0716bfca",
                   "C2": "a7e5203de58d989b97e759c014d2347d4355ebc652d696012c7552db0716bfca",
                   "B": "72e11a86f44563e9decb26fe1c586ed7d5465e98ca282ada5ae8753b1156cad5",
                   "C1": "72e11a86f44563e9decb26fe1c586ed7d5465e98ca282ada5ae8753b1156cad5"}
EXPECTED_BIN = {"A": "7fafb749dd33df446a0cb5839c5694df237312a8e8048d2e9420480164ff5934",
                "B": "b8473e168c200d676440a4079479d16d6e8583610d3447a97c1d8be9108ad981",
                "C1": "897bd02cf3b7f63107e77ea9cb64d82e92885a68f08a6d06e7ecbcc01bf0dfe9",
                "C2": "37e0b98fce8e4de7a2dc3a26b7485f76f9537b289934f29985efcf3e6e637959"}
PRIMARY = ["b389cases/s1212_fb3/fb3.bend",
           "b389cases/s1212_prlaw_nolaw/group_name_collision_semantics.bend",
           "b389cases/s1212_up_dead_forward_nolaw/group_dead_forward.bend"]
LAW_TRUE = "b389cases/s1212_fb3_law_true/fb3.bend"
F_EXPECT_PASS = {"f1179_ab": "b389cases/f1179_ab/ab.bend", "f1179_self": "b389cases/f1179_self/self.bend",
                 "f1193": "b389cases/f1193/conversion_shared_cells_once.bend",
                 "f1194_kept": "b389cases/f1194_kept/kept.bend", "f1212_g4": "b389cases/f1212_g4/g4.bend"}
F_CONTROLS = {"f1179_ba": "b389cases/f1179_ba/ba.bend", "f1194_erased": "b389cases/f1194_erased/kept_erased.bend",
              "f1194_moved": "b389cases/f1194_moved/kept_moved.bend"}
# plugin error codes that are the plugin's documented input policy, not a verdict on the release
PLUGIN_POLICY_CODES = {"unsupported_import", "unsupported_input", "invalid_input", "input_capture_failed"}


def load(path: Path) -> list[dict]:
    out = []
    gz = path.with_name(path.name + ".gz")
    if not path.exists() and gz.exists():
        import gzip
        text = gzip.decompress(gz.read_bytes()).decode()
    elif path.exists():
        text = path.read_text()
    else:
        text = ""
    if text:
        for ln in text.splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    out.append({"case": None, "class": "unparsed_line"})
    return out


def family(case: str) -> str:
    if case.startswith("b389cases/s"):
        return "S"
    if case.startswith("b389cases/f"):
        return "F"
    if case.startswith("tests/"):
        return "T"
    return "X"


def majority(vals: list):
    vals = [v if v is not None else "missing" for v in vals]
    c = collections.Counter(vals).most_common()
    if not c:
        return "missing"
    if len(vals) >= 3 and c[0][1] >= 2:
        return c[0][0]
    if len(vals) < 3:
        return vals[0]
    return "split"


def plugin_outcome(r: dict) -> str:
    st = r.get("plugin_status")
    if st != "ran":
        return st or "missing"
    if "verdict" in r:
        return "pass" if r["verdict"] == "pass" and r.get("success") else "nonpass:" + str(r["verdict"])
    return "error:" + str(r.get("code"))


def main() -> int:
    raw = Path(sys.argv[1])
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    direct = {a: collections.defaultdict(list) for a in ARMS}
    plugin = {a: collections.defaultdict(list) for a in ARMS}
    records_total = collections.Counter()
    for a in ARMS:
        for suffix in ["", "-special-r2", "-special-r3", "-confirm-r2", "-confirm-r3"]:
            for r in load(raw / f"direct-{a}{suffix}.jsonl"):
                records_total[f"direct-{a}{suffix}"] += 1
                if r.get("case"):
                    direct[a][r["case"]].append(r)
        for suffix in ["", "-special-r2", "-special-r3"]:
            for r in load(raw / f"plugin-{a}{suffix}.jsonl"):
                records_total[f"plugin-{a}{suffix}"] += 1
                if r.get("case"):
                    plugin[a][r["case"]].append(r)
    cases = sorted(set().union(*[set(direct[a]) for a in ARMS]))
    final = {a: {} for a in ARMS}
    flaky = {a: [] for a in ARMS}
    for a in ARMS:
        for c in cases:
            cls = [r.get("class") for r in direct[a].get(c, [])]
            final[a][c] = majority(cls)
            if len(set(cls)) > 1:
                flaky[a].append({"case": c, "classes": cls})
    S = {"records": dict(records_total), "arms": {}}
    for a in ARMS:
        arm = {}
        for fam, sel in [("T", lambda c: c.startswith("tests/")), ("P", lambda c: c.startswith("tests/proof/")),
                         ("X", lambda c: c.startswith(("demos/", "evals/")))]:
            cs = [c for c in cases if sel(c)]
            cnt = collections.Counter(final[a][c] for c in cs)
            arm[fam] = {"n": len(cs), "classes": dict(cnt), "agree": cnt.get("agree", 0),
                        "pass_rate": round(cnt.get("agree", 0) / len(cs), 4) if cs else None}
        # value oracles: every run's oracle on a certified case, plus PRIMARY oracles regardless of verdict
        mism, indet, evaluated, na = [], [], 0, collections.Counter()
        for c in cases:
            fam = family(c)
            if fam not in ("T", "X"):
                continue
            for r in direct[a].get(c, []):
                if r.get("class") != "agree":
                    continue
                o = r.get("oracle") or {}
                if o.get("status") != "evaluated":
                    na[o.get("status", "missing")] += 1
                    continue
                evaluated += 1
                if o["certified"] in ("different", "both"):
                    mism.append({"case": c, "certified": o["certified"], "source_value": o.get("source_value"), "type": o.get("main_type")})
                elif o["certified"] == "neither":
                    indet.append({"case": c, "source_value": o.get("source_value"), "type": o.get("main_type"),
                                  "same_out": (o.get("oracle_same") or {}).get("out", "")[:200]})
        prim = {}
        for c in PRIMARY:
            runs = direct[a].get(c, [])
            rel = [(r.get("oracle") or {}).get("certified") for r in runs]
            prim[c] = {"runs": len(runs), "certified": rel, "classes": [r.get("class") for r in runs],
                       "source_value": [(r.get("oracle") or {}).get("source_value") for r in runs]}
        derived_cases = sorted({m["case"] for m in mism})
        arm["oracles"] = {"derived_evaluated_runs": evaluated, "derived_not_applicable_runs": dict(na),
                          "derived_cases_evaluated": len({c for c in cases if family(c) in ("T", "X") and any(
                              (r.get("oracle") or {}).get("status") == "evaluated" and r.get("class") == "agree"
                              for r in direct[a].get(c, []))}),
                          "translation_mismatch_derived": mism, "indeterminate_derived": indet, "primary": prim}
        prim_mismatch = sum(1 for c in PRIMARY if any(x in ("different", "both") for x in prim[c]["certified"]))
        arm["translation_mismatch_count"] = len(derived_cases) + prim_mismatch
        arm["translation_mismatch_runs"] = len(mism) + sum(
            sum(1 for x in prim[c]["certified"] if x in ("different", "both")) for c in PRIMARY)
        # S family verdicts and F family
        sfam = {}
        for c in cases:
            if family(c) == "S":
                runs = direct[a].get(c, [])
                sfam[c] = {"classes": [r.get("class") for r in runs],
                           "certified": [(r.get("oracle") or {}).get("certified") for r in runs],
                           "plugin": [plugin_outcome(r) for r in plugin[a].get(c, [])],
                           "crash": any("TypeError" in (r.get("verdict_out") or "") for r in runs)}
        arm["S"] = sfam
        fn = {}
        for name, c in {**F_EXPECT_PASS, **F_CONTROLS}.items():
            runs = direct[a].get(c, [])
            fn[name] = {"expected": "pass", "control": name in F_CONTROLS,
                        "direct_classes": [r.get("class") for r in runs],
                        "direct_reason": [r.get("reason", "")[:120] for r in runs][:1],
                        "crash": any("TypeError" in (r.get("verdict_out") or "") for r in runs),
                        "plugin": [plugin_outcome(r) for r in plugin[a].get(c, [])]}
            fn[name]["false_negative"] = majority(fn[name]["direct_classes"]) != "agree"
        arm["F"] = fn
        arm["false_negatives_F"] = sorted(k for k, v in fn.items() if v["false_negative"] and not v["control"])
        arm["false_negative_controls_failed"] = sorted(k for k, v in fn.items() if v["false_negative"] and v["control"])
        arm["corpus_false_rejects_T"] = sorted(c for c in cases if c.startswith("tests/") and final[a][c] == "!")
        arm["corpus_timeouts_T"] = sorted(c for c in cases if c.startswith("tests/") and final[a][c] == "t")
        arm["corpus_out_of_scope_T"] = sorted(c for c in cases if c.startswith("tests/") and final[a][c] == "-")
        arm["corpus_nonagree_X"] = {c: final[a][c] for c in cases if family(c) == "X" and final[a][c] in ("!", "t", "-")}
        arm["flaky"] = flaky[a]
        # plugin consistency on the primary pass (first plugin record per case)
        pc = collections.Counter()
        false_pass, false_reject, policy = [], [], collections.Counter()
        for c in cases:
            pr = plugin[a].get(c)
            if not pr:
                pc["missing"] += 1
                continue
            po = plugin_outcome(pr[0])
            pc[po.split(":")[0]] += 1
            d = final[a][c]
            if po == "pass" and d != "agree":
                false_pass.append({"case": c, "direct": d})
            elif po.startswith("error:") and po[6:] in PLUGIN_POLICY_CODES:
                policy[po[6:] + " (direct " + str(d) + ")"] += 1
            elif po not in ("pass", "layout_na") and d == "agree":
                false_reject.append({"case": c, "plugin": po})
        n_pl = sum(v for k, v in pc.items() if k not in ("missing", "layout_na"))
        arm["plugin"] = {"outcomes": dict(pc), "ran": n_pl, "pass_rate_over_ran": round(pc.get("pass", 0) / n_pl, 4) if n_pl else None,
                         "plugin_false_pass": false_pass, "plugin_nonpass_on_direct_agree": false_reject,
                         "plugin_policy_errors": dict(policy)}
        # identities
        ids = []
        for suffix in ["", "-special-r2", "-special-r3", "-confirm-r2", "-confirm-r3"]:
            for m in load(raw / f"direct-{a}{suffix}.meta.jsonl"):
                if m.get("event") in ("start", "end"):
                    ids.append(m["identity"])
        kern = sorted({i.get("kernel_sha256") for i in ids})
        bins = sorted({i.get("bend_sha256") for i in ids})
        pk = set()
        for suffix in ["", "-special-r2", "-special-r3"]:
            for m in load(raw / f"plugin-{a}{suffix}.meta.jsonl"):
                if m.get("phase") == "bootstrap":
                    pk.add(m.get("kernel_sha256_after"))
            for r in load(raw / f"plugin-{a}{suffix}.jsonl"):
                if r.get("kernel_sha256_after"):
                    pk.add(r["kernel_sha256_after"])
        arm["identity"] = {"direct_kernel_sha256": kern, "direct_bin_sha256": bins, "plugin_kernel_sha256": sorted(pk),
                           "kernel_as_expected": kern == [EXPECTED_KERNEL[a]] and sorted(pk) == [EXPECTED_KERNEL[a]],
                           "bin_as_expected": bins == [EXPECTED_BIN[a]]}
        S["arms"][a] = arm
    # differentials (stable = not flaky on either arm)
    flaky_set = {a: {f["case"] for f in flaky[a]} for a in ARMS}

    def diff(x, y):
        lost, gained = [], []
        for c in cases:
            if family(c) not in ("T", "X"):
                continue
            fx, fy = final[x][c], final[y][c]
            stable = c not in flaky_set[x] and c not in flaky_set[y]
            if fx == "agree" and fy != "agree":
                lost.append({"case": c, x: fx, y: fy, "stable": stable})
            elif fx != "agree" and fy == "agree":
                gained.append({"case": c, x: fx, y: fy, "stable": stable})
        return lost, gained

    lost, gained = diff("A", "B")
    for item in lost + gained:
        c = item["case"]
        item["C1"], item["C2"] = final["C1"][c], final["C2"][c]
        if item in lost:
            item["attribution"] = ("upstream delta (also lost without the patch on 01875127)" if final["C1"][c] != "agree"
                                   else "the 2-line patch (01875127 keeps it)")
            item["patch_on_stock"] = final["C2"][c]
        else:
            item["attribution"] = ("upstream delta (01875127 also gains it)" if final["C1"][c] == "agree"
                                   else "the 2-line patch (01875127 does not)")
    c2l, c2g = diff("A", "C2")
    c1l, c1g = diff("C1", "B")
    S["differential"] = {"A_to_B_lost": lost, "A_to_B_gained": gained,
                         "A_to_C2_lost (patch alone on stock)": c2l, "A_to_C2_gained": c2g,
                         "C1_to_B_lost (patch alone on main)": c1l, "C1_to_B_gained": c1g,
                         "new_failures_introduced_by_candidate_stable": len([x for x in lost if x["stable"]]),
                         "new_failures_attributed_to_patch": len([x for x in lost if x["stable"] and "2-line" in x["attribution"]])}
    # CLI
    cli = {}
    cdir = raw / "cli"
    if cdir.exists():
        for d in sorted(cdir.iterdir()):
            rec = {"exit_code": (d / "exit_code").read_text().strip() if (d / "exit_code").exists() else None}
            try:
                j = json.loads((d / "stdout.json").read_text())
                rec.update({k: j.get(k) for k in ("verdict", "success", "code", "kernel_strategy", "kernel_sha256_after", "bend_sha256", "source_semantics_attested")})
            except (OSError, ValueError):
                rec["parse"] = "failed"
            cli[d.name] = rec
    S["cli"] = cli
    # acceptance
    acc = {}
    for a in ARMS:
        arm = S["arms"][a]
        p = arm["oracles"]["primary"]
        g1 = all(v["runs"] >= 3 and all(x == "source" for x in v["certified"]) for v in p.values())
        g2 = not arm["oracles"]["translation_mismatch_derived"] and not arm["oracles"]["indeterminate_derived"]
        lt = arm["S"].get(LAW_TRUE, {})
        g3 = bool(lt.get("classes")) and all(x != "agree" for x in lt["classes"]) and all(x != "pass" for x in lt.get("plugin", []))
        sem = g1 and g2 and g3
        rec = {"g1_primary_oracles_certify_source_all_reps": g1, "g2_no_derived_mismatch_or_indeterminate": g2,
               "g3_law_true_never_passes": g3, "promotable_semantic_gate_389": sem}
        if a == "B":
            rec["no_stable_new_failures_vs_stock"] = S["differential"]["new_failures_introduced_by_candidate_stable"] == 0
        rec["no_plugin_false_pass"] = not arm["plugin"]["plugin_false_pass"]
        rec["identities_as_expected"] = arm["identity"]["kernel_as_expected"] and arm["identity"]["bin_as_expected"]
        rec["preferred_verifier_recommendation"] = sem and rec["no_plugin_false_pass"] and rec["identities_as_expected"] and rec.get("no_stable_new_failures_vs_stock", True)
        acc[a] = rec
    S["acceptance"] = acc
    text = json.dumps(S, indent=1, sort_keys=True)
    if out_path:
        out_path.write_text(text + "\n")
    brief = {a: {"T_pass_rate": S["arms"][a]["T"]["pass_rate"], "P_pass_rate": S["arms"][a]["P"]["pass_rate"],
                 "X_pass_rate": S["arms"][a]["X"]["pass_rate"], "translation_mismatch": S["arms"][a]["translation_mismatch_count"],
                 "FN_F": S["arms"][a]["false_negatives_F"], "accept": acc[a]} for a in ARMS}
    print(json.dumps(brief, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
