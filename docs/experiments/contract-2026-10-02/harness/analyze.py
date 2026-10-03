"""Contract-lane analysis over the packet's sanitized raw/ tree (no local files needed).

usage: python3 analyze.py <packet_dir> [--out summary.json]
Implements PREREG.json: normalizations N1-N3 (N4 was applied at export), metrics M1-M6, the attribution rule,
H1 (paired payload / tool-call comparison with A/A control), H2 chat-level block-directive count, H5 canary scan
and H6 footprint. In-process sections (raw/inproc/*.json) are summarised as recorded.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from math import comb
from pathlib import Path

CANARIES = {"prompt": "ctr-canary-prompt-7f3a", "tool_result": "ctr-canary-tool-91c2",
            "readme": "ctr-canary-readme-5d0e", "bend_source": "ctr-canary-src-b81e", "cua_title": "CuaTestHarness",
            # deviation D1 (recorded before measured analysis): 'CuaTestHarness' is in the verbatim CUA prompt, so it
            # is judged under the prompt rule; 'Save note' is an AX label that only reaches Hermes in tool results.
            "cua_tool_result": "Save note",
            # repository document content (README open item) persisted by the bundled State Packet: measured only
            "repo_doc_item": "Ship the next release"}
PROMPT_LIKE = {"prompt", "cua_title"}
MEASURED_ONLY = {"readme", "repo_doc_item"}
DATE = re.compile(r"Conversation started: [^\n]*")
N3 = [(re.compile(r'"duration_ms": ?-?[0-9.eE+-]+'), '"duration_ms": "<N>"'),
      (re.compile(r'"receipt_id": ?"[0-9a-f]+"'), '"receipt_id": "<ID>"'),
      (re.compile(r'"verified_at": ?"[^"]*"'), '"verified_at": "<TS>"'),
      (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
      (re.compile(r"pid \d+"), "pid <PID>"), (re.compile(r'"pid": ?\d+'), '"pid": "<PID>"')]


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def body(packet: Path, digest: str | None) -> bytes:
    return gzip.decompress((packet / "raw" / "bodies" / f"{digest}.gz").read_bytes()) if digest else b""


def parse_sse(raw: bytes) -> dict:
    """Assistant content, tool calls and usage from a streamed (or plain) chat completion."""
    text = raw.decode("utf-8", "replace")
    content, calls, usage, finish = [], {}, None, None
    chunks = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")] if "data:" in text else [text]
    for chunk in chunks:
        if not chunk or chunk == "[DONE]":
            continue
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        usage = obj.get("usage") or usage
        for choice in obj.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            finish = choice.get("finish_reason") or finish
            if delta.get("content"):
                content.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "arguments": ""})
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
    out_calls = []
    for _, c in sorted(calls.items()):
        try:
            args = json.loads(c["arguments"]) if c["arguments"] else {}
        except ValueError:
            args = {"_unparsed": c["arguments"]}
        out_calls.append({"name": c["name"], "args": args})
    return {"content": "".join(content), "tool_calls": out_calls, "usage": usage, "finish_reason": finish}


def normalize(req: dict, tool_results: bool) -> dict:
    """N1 + N2 (+ N3 inside tool message content when tool_results)."""
    req = json.loads(json.dumps(req))
    ids = {}

    def ordinal(x):
        return ids.setdefault(x, f"call#{len(ids) + 1}")
    for m in req.get("messages", []):
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            m["content"] = DATE.sub("Conversation started: <DATE>", m["content"])
        for tc in m.get("tool_calls") or []:
            if "id" in tc:
                tc["id"] = ordinal(tc["id"])
        if m.get("tool_call_id"):
            m["tool_call_id"] = ordinal(m["tool_call_id"])
        if tool_results and m.get("role") == "tool" and isinstance(m.get("content"), str):
            for rx, rep in N3:
                m["content"] = rx.sub(rep, m["content"])
    return req


def components(req: dict) -> dict:
    msgs = req.get("messages", [])
    system = next((m.get("content") for m in msgs if m.get("role") == "system"), None)
    user = next((m.get("content") for m in msgs if m.get("role") == "user"), None)
    return {"system": sha(DATE.sub("Conversation started: <DATE>", system) if isinstance(system, str) else system),
            "tools": sha(req.get("tools")), "params": sha({k: v for k, v in req.items() if k not in ("messages", "tools")}),
            "first_user": sha(user)}


def load_run(packet: Path, rd: Path) -> dict:
    run = json.loads((rd / "run.json").read_text())
    rows = [json.loads(ln) for ln in (rd / "capture.jsonl").read_text().splitlines()] if (rd / "capture.jsonl").exists() else []
    mains, aux = [], []
    for row in rows:
        if row["method"] != "POST" or not row["path"].rstrip("/").endswith("/chat/completions"):
            continue
        try:
            req = json.loads(body(packet, row["req_body"]))
        except ValueError:
            continue
        resp = parse_sse(body(packet, row["resp_body"]))
        item = {"n": row["n"], "req": req, "resp": resp, "status": row["status"], "error": row["error"]}
        (mains if "tools" in req else aux).append(item)
    oracle = json.loads((rd / "oracle.json").read_text()) if (rd / "oracle.json").exists() else {"verdict": "unknown"}
    rc = (rd / "meta" / "exit_code").read_text().strip() if (rd / "meta" / "exit_code").exists() else None
    calls = [c for m in mains for c in m["resp"]["tool_calls"]]
    blocked = sum(1 for m in mains for msg in m["req"].get("messages", [])
                  if msg.get("role") == "tool" and "pre_tool_call plugin callback" in str(msg.get("content")))
    return {"run": run, "mains": mains, "aux": aux, "oracle": oracle.get("verdict"), "rc": rc,
            "first": sha(normalize(mains[0]["req"], False)) if mains else None,
            "comp": [components(m["req"]) for m in mains],
            "traj": [sha(normalize(m["req"], True)) for m in mains],
            "resp_traj": [sha({"c": m["resp"]["content"], "t": m["resp"]["tool_calls"]}) for m in mains],
            "calls_full": [(c["name"], json.dumps(c["args"], sort_keys=True)) for c in calls],
            "calls_names": [c["name"] for c in calls],
            "prompt_tokens": [((m["resp"]["usage"] or {}).get("prompt_tokens")) for m in mains],
            "blocked_tool_results": blocked, "rd": rd}


def first_divergence(a: dict, b: dict) -> dict:
    """Preregistered attribution rule for one pair."""
    hermes_built = []
    for k, (ca, cb) in enumerate(zip(a["comp"], b["comp"])):
        for part in ("system", "tools", "params", "first_user"):
            if ca[part] != cb[part]:
                hermes_built.append({"k": k + 1, "part": part})
    if hermes_built:
        return {"class": "hermes_built", "detail": hermes_built[:5]}
    for k in range(max(len(a["traj"]), len(b["traj"]))):
        if k >= len(a["traj"]) or k >= len(b["traj"]):
            return {"class": "length", "k": k + 1}
        if a["traj"][k] != b["traj"][k]:
            ma = normalize(a["mains"][k]["req"], True)["messages"]
            mb = normalize(b["mains"][k]["req"], True)["messages"]
            for i, (x, y) in enumerate(zip(ma, mb)):
                if x != y:
                    role = x.get("role")
                    cls = {"assistant": "model", "tool": "tool_result"}.get(role, "hermes_built")
                    return {"class": cls, "k": k + 1, "message_index": i, "role": role}
            return {"class": "hermes_built" if len(ma) == len(mb) else "model", "k": k + 1,
                    "message_index": min(len(ma), len(mb)), "role": "count"}
        if a["resp_traj"][k] != b["resp_traj"][k]:
            # identical request k, different model response k: divergence starts in the model
            later = k + 1 < len(a["traj"]) and k + 1 < len(b["traj"])
            return {"class": "model", "k": k + 1, "role": "response", "continues": later}
    return {"class": "identical"}


def fisher_two_sided(a, b, c, d) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]."""
    n1, n2, k, n = a + b, c + d, a + c, a + b + c + d

    def p(x):
        return comb(n1, x) * comb(n2, k - x) / comb(n, k)
    obs = p(a)
    lo, hi = max(0, k - n2), min(k, n1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs * (1 + 1e-9)))


def main():
    packet = Path(sys.argv[1])
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else packet / "summary.json"
    plan = json.loads((packet / "plan.json").read_text())
    runs = {}
    for rd in sorted((packet / "raw" / "runs").iterdir()):
        if (rd / "run.json").exists():
            runs[rd.name] = load_run(packet, rd)
    planned = [r for r in plan["runs"] if r["set"] == "measured"]
    missing = [r["run_id"] for r in plan["runs"] if r["run_id"] not in runs]
    summary = {"schema": "bend_native.contract.summary.v1", "planned_measured": len(planned),
               "executed_measured": sum(1 for r in planned if r["run_id"] in runs), "missing_runs": missing,
               "per_run": {}, "H1": {}, "H2_chat": {}, "H5": {}, "H6": {}, "outcomes": {}, "prompt_tokens": {}}
    for rid, r in runs.items():
        summary["per_run"][rid] = {"task": r["run"]["task"], "arm": r["run"]["arm"], "pair": r["run"]["pair"],
                                   "set": r["run"]["set"], "rc": r["rc"], "oracle": r["oracle"],
                                   "main_requests": len(r["mains"]), "aux_requests": len(r["aux"]),
                                   "first_request_sha256": r["first"], "tool_calls": r["calls_names"],
                                   "max_prompt_tokens": max([t for t in r["prompt_tokens"] if t] or [0]),
                                   "blocked_tool_results": r["blocked_tool_results"]}
    # ---- H1 ----
    pairs = {"off_vs_shadow": [], "off_vs_aa": [], "shadow_vs_aa": []}
    by = {}
    for rid, r in runs.items():
        if r["run"]["set"] == "measured":
            by[(r["run"]["task"], r["run"]["pair"], r["run"]["arm"])] = r
    tasks = sorted({k[0] for k in by})
    for task in tasks:
        for pair in sorted({k[1] for k in by if k[0] == task}):
            for label, (x, y) in (("off_vs_shadow", ("off", "shadow")), ("off_vs_aa", ("off", "aa")),
                                  ("shadow_vs_aa", ("shadow", "aa"))):
                a, b = by.get((task, pair, x)), by.get((task, pair, y))
                if a is None or b is None:
                    pairs[label].append({"task": task, "pair": pair, "class": "missing_run"})
                    continue
                pairs[label].append({"task": task, "pair": pair, **first_divergence(a, b),
                                     "first_request_equal": a["first"] == b["first"],
                                     "tool_calls_full_equal": a["calls_full"] == b["calls_full"],
                                     "tool_calls_names_equal": a["calls_names"] == b["calls_names"],
                                     "outcomes": [a["oracle"], b["oracle"]]})
    first_hashes = {}
    for (task, pair, arm), r in by.items():
        first_hashes.setdefault(task, {}).setdefault(r["first"], []).append(f"{arm}-p{pair:02d}")
    h1 = {"pairs": pairs, "first_request_distinct_hashes_per_task": {t: {h: len(v) for h, v in d.items()}
                                                                      for t, d in first_hashes.items()}}
    table = {}
    for label, rows in pairs.items():
        for scope in tasks + ["pooled"]:
            sel = [p for p in rows if scope == "pooled" or p["task"] == scope]
            cls = {}
            for p in sel:
                cls[p["class"]] = cls.get(p["class"], 0) + 1
            table.setdefault(label, {})[scope] = {
                "n": len(sel), "classes": cls,
                "diverged": sum(1 for p in sel if p["class"] not in ("identical", "missing_run")),
                "tool_calls_full_differ": sum(1 for p in sel if p.get("tool_calls_full_equal") is False),
                "tool_calls_names_differ": sum(1 for p in sel if p.get("tool_calls_names_equal") is False),
                "outcome_discordant": sum(1 for p in sel if p.get("outcomes") and p["outcomes"][0] != p["outcomes"][1]),
                "hermes_built": cls.get("hermes_built", 0)}
    tests = {}
    for scope in tasks + ["pooled"]:
        s, c = table["off_vs_shadow"][scope], table["off_vs_aa"][scope]
        tests[scope] = {}
        for metric in ("diverged", "tool_calls_full_differ", "tool_calls_names_differ", "outcome_discordant"):
            tests[scope][metric] = {"off_vs_shadow": f"{s[metric]}/{s['n']}", "off_vs_aa": f"{c[metric]}/{c['n']}",
                                    "fisher_two_sided_p": round(fisher_two_sided(s[metric], s["n"] - s[metric],
                                                                                 c[metric], c["n"] - c[metric]), 4)}
    h1["table"], h1["tests"] = table, tests
    h1["pass"] = (table["off_vs_shadow"]["pooled"]["hermes_built"] == 0
                  and all(len(d) == 1 for d in first_hashes.values())
                  and not any(p["class"] == "missing_run" for p in pairs["off_vs_shadow"]))
    summary["H1"] = h1
    # ---- H2 (chat) ----
    summary["H2_chat"] = {"runs": len(runs), "tool_results_with_block_directive":
                          sum(r["blocked_tool_results"] for r in runs.values())}
    # ---- outcomes / tokens ----
    for (task, pair, arm), r in by.items():
        o = summary["outcomes"].setdefault(task, {}).setdefault(arm, {})
        o[r["oracle"]] = o.get(r["oracle"], 0) + 1
    toks = [t for r in runs.values() for t in r["prompt_tokens"] if t]
    summary["prompt_tokens"] = {"max": max(toks) if toks else None, "requests_ge_32768": sum(1 for t in toks if t >= 32768),
                                "requests_with_usage": len(toks)}
    # ---- H5 ----
    h5 = {"per_run": {}, "violations": []}
    for rid, r in runs.items():
        pdata = r["rd"] / "plugin-data"
        hits = {}
        if pdata.exists():
            for f in sorted(p for p in pdata.rglob("*") if p.is_file()):
                text = f.read_text(errors="replace")
                found = [name for name, needle in CANARIES.items() if needle in text]
                if found:
                    hits[str(f.relative_to(pdata))] = found
        z0 = sorted(str(p.relative_to(pdata)) for p in (pdata / "bend" / "z0").rglob("*")) if (pdata / "bend" / "z0").exists() else []
        arm = r["run"]["arm"]
        h5["per_run"][rid] = {"arm": arm, "canary_hits": hits, "z0_paths": len(z0)}
        for path, found in hits.items():
            for name in found:
                allowed = name in PROMPT_LIKE and arm == "shadow" and path.endswith("z0/opportunities.jsonl")
                if not allowed and name not in MEASURED_ONLY:
                    h5["violations"].append({"run": rid, "path": path, "canary": name})
        if arm in ("off", "aa", "disabled") and z0:
            h5["violations"].append({"run": rid, "path": "bend/z0", "canary": "z0 data written while stack off"})
    for name in sorted(MEASURED_ONLY):
        paths = sorted({re.sub(r"[0-9a-f]{16}", "<repo-sha16>", p) for d in h5["per_run"].values()
                        for p, f in d["canary_hits"].items() if name in f})
        h5[f"{name}_paths"] = paths
        h5[f"{name}_runs"] = sum(1 for d in h5["per_run"].values() if any(name in f for f in d["canary_hits"].values()))
    h5["prompt_canary_in_shadow_opportunities"] = sum(
        1 for d in h5["per_run"].values() if d["arm"] == "shadow"
        and any(p.endswith("opportunities.jsonl") and "prompt" in f for p, f in d["canary_hits"].items()))
    h5["pass"] = not h5["violations"]
    summary["H5"] = h5
    # ---- H6 ----
    h6 = {"runs": {}}
    for rid, r in runs.items():
        if r["run"]["arm"] != "disabled":
            continue
        listing = (r["rd"] / "meta" / "home-upper.list").read_text().splitlines() if (r["rd"] / "meta" / "home-upper.list").exists() else []
        bendish = [p for p in listing if "bend" in p.lower()]
        h6["runs"][rid] = {"bend_paths": bendish, "plugin_data_files": len(list((r["rd"] / "plugin-data").rglob("*")))
                           if (r["rd"] / "plugin-data").exists() else 0, "rc": r["rc"], "oracle": r["oracle"]}
    h6["pass"] = bool(h6["runs"]) and all(not v["bend_paths"] and v["plugin_data_files"] == 0 for v in h6["runs"].values())
    summary["H6"] = h6
    # ---- in-process records ----
    ip = packet / "raw" / "inproc"
    summary["inproc"] = {f.stem: json.loads(f.read_text()) for f in sorted(ip.glob("*.json"))} if ip.exists() else {}
    for v in summary["per_run"].values():
        v["tool_calls"] = v["tool_calls"][:20]
    out_path.write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({"H1_pass": h1["pass"], "H5_pass": h5["pass"], "H6_pass": h6["pass"],
                      "off_vs_shadow": table["off_vs_shadow"]["pooled"], "off_vs_aa": table["off_vs_aa"]["pooled"]},
                     indent=1))


if __name__ == "__main__":
    main()
