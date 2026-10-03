"""(bend-e2e copy of the accepted CONFIRM build_turn_examples.py: oracle id, run filter and slice fields changed;
the question, the state counters and the outcome join are unchanged. Was: CONFIRM copy of the SAMPLES builder: observer session-id
fallback counted in the audit; the request and the join are unchanged.)

Build verification_needed examples (one per Hermes task turn) from the frozen dataset.

  python build_turn_examples.py <dataset_dir> <z0int_worktree> --output <examples.jsonl> --audit-output <audit.json>

Glue only: the outcome join uses the existing z0intelligence adapters.hermes_z0int functions
(normalize_envelope -> close_observation -> join_outcome) and the question is the existing
decision-capability-v1 verification_needed question, verbatim. State = content-free counters from
the turn's own observer rows; no prompt, family, file content or oracle field enters the request.
Rows are z0int.hermes_shadow_example.v1, scored by the #386 score_examples and evaluated by
evaluate_shadow --question-id verification_needed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

QUESTION = {
    "id": "verification_needed",
    "type": "boolean",
    "instructions": "Does this turn require an explicit verification pass before responding?",
    "criteria": {"false": "Safe to respond without extra verification", "true": "Run verification before responding"},
}
ORACLE_ID = "independent_oracle:bend-e2e-2026-10-02"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def turn_state(rows: list[dict]) -> tuple[dict, dict]:
    """(request state, identity/outcome facts) from one session's observer rows, in file order."""
    pres = [r for r in rows if r.get("event") == "pre_api_request"]
    posts = [r for r in rows if r.get("event") == "post_api_request"]
    errors = [r for r in rows if r.get("event") == "api_request_error"]
    tools = [r for r in rows if r.get("event") == "post_tool_call"]
    ends = [r for r in rows if r.get("event") == "on_session_end"]
    f = lambda r: r.get("fields") or {}  # noqa: E731
    names = sorted({(r.get("tool") or {}).get("tool_name") or "" for r in tools} - {""})
    end = f(ends[-1]) if ends else {}
    first = pres[0] if pres else (rows[0] if rows else {})
    state = {
        "harness": "hermes",
        "provider": f(first).get("provider"),
        "model": f(first).get("model"),
        "api_call_count": len(pres),
        "api_error_count": len(errors),
        "max_retry_count": max([int(f(r).get("retry_count") or 0) for r in pres] or [0]),
        "tool_call_count": len(tools),
        "tool_error_count": sum(1 for r in tools if (r.get("tool") or {}).get("status") not in (None, "ok")),
        "tools_used": names,
        "distinct_tool_count": len(names),
        "final_finish_reason": f(posts[-1]).get("finish_reason") if posts else None,
        "execution_completed": bool(end.get("completed")) if ends else False,
        "turn_exit_reason": end.get("turn_exit_reason"),
        "interrupted": bool(end.get("interrupted")) if ends else None,
        "approx_input_tokens_max": max([int(f(r).get("approx_input_tokens") or 0) for r in pres] or [0]),
        "prompt_tokens_last": (posts[-1].get("usage") or {}).get("prompt_tokens") if posts else None,
        "output_tokens_total": sum(int((r.get("usage") or {}).get("output_tokens") or 0) for r in posts),
    }
    state = {k: v for k, v in state.items() if v is not None}
    ident = first.get("identity") or {}
    facts = {"session_id": ident.get("session_id"), "turn_id": ident.get("turn_id"), "trace_id": ident.get("trace_id"),
             "execution_completed": state["execution_completed"], "model": state.get("model"), "has_session_end": bool(ends)}
    return state, facts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("z0int_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()
    for p in (args.z0int_root, args.z0int_root / "src"):
        sys.path.insert(0, str(p.resolve()))
    from adapters.hermes_z0int import close_observation, join_outcome, normalize_envelope

    runs = read_jsonl(args.dataset / "runs.jsonl")
    by_session: dict[str, list[dict]] = defaultdict(list)
    for row in read_jsonl(args.dataset / "events.jsonl"):
        sid = (row.get("identity") or {}).get("session_id")
        if sid:
            by_session[sid].append(row)

    audit = {"n_tasks": len(runs), "n_not_run": 0, "n_run": 0, "n_without_session_id": 0,
             "n_without_api_rows": 0, "n_examples": 0, "n_label_known": 0, "n_positive": 0,
             "n_negative": 0, "n_unknown_label": 0, "n_session_id_from_observer": 0,
             "n_session_id_mismatch": 0}
    examples = []
    for run in runs:
        if run.get("task") not in ("cua_browser", "cua_gtk3", "bend_fix", "bend_contra") or run.get("set") != "measured":
            continue
        if run.get("status") != "RUN":
            audit["n_not_run"] += 1
            continue
        audit["n_run"] += 1
        sid = run.get("session_id") or ""
        if not sid and len(run.get("observer_session_ids") or []) == 1:  # stdout lacked the session_id line
            sid = run["observer_session_ids"][0]
            audit["n_session_id_from_observer"] += 1
        if sid and run.get("observer_session_ids") and sid not in run["observer_session_ids"]:
            audit["n_session_id_mismatch"] += 1
        rows = by_session.get(sid, []) if sid else []
        if not sid:
            audit["n_without_session_id"] += 1
        if not any(r.get("event") == "pre_api_request" for r in rows):
            audit["n_without_api_rows"] += 1
        state, facts = turn_state(rows)
        env = normalize_envelope({"session_id": sid or None, "turn_id": facts["turn_id"], "trace_id": facts["trace_id"],
                                  "capability_id": QUESTION["id"]})
        obs = close_observation(env, execution_completed=facts["execution_completed"], verified_success=None,
                                provider="hermes", model=facts["model"])
        joined = join_outcome(obs, gold_verified=run.get("success") if run.get("oracle") != "unknown" else None)
        verified = joined.get("verified_success")
        label = None if verified is None else (verified is False)
        audit["n_examples"] += 1
        if label is None:
            audit["n_unknown_label"] += 1
        else:
            audit["n_label_known"] += 1
            audit["n_positive" if label else "n_negative"] += 1
        examples.append({
            "schema": "z0int.hermes_shadow_example.v1",
            "question_id": QUESTION["id"],
            "identity": {"harness_id": "hermes", "session_id": sid or None, "turn_id": facts["turn_id"],
                         "trace_id": facts["trace_id"], "work_item_id": run["run_id"]},
            "request": {"state": state, "questions": [QUESTION], "request_id": facts["trace_id"] or run["run_id"]},
            "verified_outcome": label,
            "outcome_source": ORACLE_ID,
            "observation": {k: joined.get(k) for k in ("schema", "harness_id", "session_id", "trace_id", "turn_id",
                                                       "capability_id", "model", "execution_completed",
                                                       "verified_success", "outcome_join")},
            "slice": {"task": run.get("task"), "rep": run.get("rep"),
                      "execution_completed": facts["execution_completed"]},
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for ex in examples:
            stream.write(json.dumps(ex, sort_keys=True) + "\n")
    args.audit_output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    main()
