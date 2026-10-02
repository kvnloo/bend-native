"""Build and optionally score the first z0 DecisionBackend shadow dataset.

Question family: api.attempt_will_fail
Gold label: objective Hermes observer event (api_request_error=True,
post_api_request=False).  The model prediction never controls Hermes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

QUESTION_ID = "api.attempt_will_fail"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _key(row: Mapping[str, Any]) -> tuple[str, int]:
    ident = row.get("identity") if isinstance(row.get("identity"), Mapping) else {}
    fields = row.get("fields") if isinstance(row.get("fields"), Mapping) else {}
    return str(ident.get("api_request_id") or ""), int(fields.get("api_call_count") or 0)


def request_for(pre: Mapping[str, Any]) -> dict[str, Any]:
    fields = pre.get("fields") if isinstance(pre.get("fields"), Mapping) else {}
    ident = pre.get("identity") if isinstance(pre.get("identity"), Mapping) else {}
    state = {
        "harness": "hermes",
        "provider": fields.get("provider"),
        "model": fields.get("model"),
        "api_mode": fields.get("api_mode"),
        "api_call_count": fields.get("api_call_count"),
        "retry_count": fields.get("retry_count"),
        "message_count": fields.get("message_count"),
        "tool_count": fields.get("tool_count"),
        "approx_input_tokens": fields.get("approx_input_tokens"),
        "request_char_count": fields.get("request_char_count"),
        "max_tokens": fields.get("max_tokens"),
        "platform": fields.get("platform"),
    }
    # Remove absent fields so "unknown" stays different from zero.
    state = {k: v for k, v in state.items() if v is not None}
    return {
        "state": state,
        "questions": [{
            "id": QUESTION_ID,
            "type": "boolean",
            "instructions": (
                "Predict whether this physical Hermes provider attempt will fail "
                "before a normalized successful response is returned."
            ),
            "criteria": {
                "false": "The attempt returns a normalized provider response.",
                "true": "The attempt emits api_request_error before success.",
            },
        }],
        "request_id": str(ident.get("api_request_id") or ""),
    }


def joined_examples(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pending: dict[tuple[str, int], Mapping[str, Any]] = {}
    examples: list[dict[str, Any]] = []
    for row in rows:
        event = row.get("event")
        key = _key(row)
        if not key[0]:
            continue
        if event == "pre_api_request":
            pending[key] = row
            continue
        if event not in {"post_api_request", "api_request_error"}:
            continue
        pre = pending.pop(key, None)
        if pre is None:
            continue
        outcome = event == "api_request_error"
        examples.append({
            "schema": "z0int.hermes_shadow_example.v1",
            "question_id": QUESTION_ID,
            "identity": pre.get("identity"),
            "request": request_for(pre),
            "verified_outcome": outcome,
            "outcome_source": event,
        })
    return examples


def score_examples(examples: list[dict[str, Any]], backend_name: str, z0int_root: Path | None) -> list[dict[str, Any]]:
    if z0int_root is not None:
        src = str((z0int_root / "src").resolve())
        if src not in sys.path:
            sys.path.insert(0, src)
    from z0int.backends.base import request_from_mapping, result_to_dict
    from z0int.backends.registry import create_backend

    backend = create_backend(backend_name)
    out = []
    for example in examples:
        result = backend.evaluate(request_from_mapping(example["request"]))
        row = dict(example)
        row["decision"] = result_to_dict(result)
        out.append(row)
    return out


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend")
    parser.add_argument("--z0int-root", type=Path, default=(Path(os.environ["Z0INT_ROOT"]) if os.environ.get("Z0INT_ROOT") else None))
    args = parser.parse_args()

    examples = joined_examples(read_jsonl(args.events))
    rows = score_examples(examples, args.backend, args.z0int_root) if args.backend else examples
    write_jsonl(args.output, rows)
    print(json.dumps({"examples": len(rows), "backend": args.backend, "output": str(args.output)}))


if __name__ == "__main__":
    main()
