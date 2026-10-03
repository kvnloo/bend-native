"""Evaluate scored z0 DecisionBackend shadow examples without self-labeling."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

QUESTION_ID = "api.attempt_will_fail"


def _probability(row: Mapping[str, Any], question_id: str = QUESTION_ID) -> float | None:
    decision = row.get("decision")
    if not isinstance(decision, Mapping):
        return None
    answers = decision.get("answers")
    if not isinstance(answers, list):
        return None
    for answer in answers:
        if not isinstance(answer, Mapping) or answer.get("question_id") != question_id:
            continue
        probs = answer.get("probabilities")
        if not isinstance(probs, Mapping):
            return None
        for key in ("true", "True", "1", True):
            if key in probs:
                value = probs[key]
                return float(value) if isinstance(value, (int, float)) else None
    return None


def evaluate(rows: Iterable[Mapping[str, Any]], question_id: str = QUESTION_ID) -> dict[str, Any]:
    pairs: list[tuple[float, int]] = []
    latencies: list[float] = []
    backend = None
    # Every row lands in exactly one bucket; nothing is dropped silently.
    counts = {"n_rows": 0, "n_labelled": 0, "n_unknown_label": 0, "n_backend_error": 0, "n_missing_probability": 0}
    for row in rows:
        counts["n_rows"] += 1
        y = row.get("verified_outcome")
        if not isinstance(y, bool):
            counts["n_unknown_label"] += 1
            continue
        counts["n_labelled"] += 1
        if row.get("backend_error"):
            counts["n_backend_error"] += 1
            continue
        p = _probability(row, question_id)
        if p is None or not math.isfinite(p) or not 0.0 <= p <= 1.0:
            counts["n_missing_probability"] += 1
            continue
        pairs.append((p, int(y)))
        decision = row.get("decision")
        if isinstance(decision, Mapping):
            backend = backend or decision.get("backend")
            latency = decision.get("latency_ms")
            if isinstance(latency, (int, float)) and math.isfinite(float(latency)):
                latencies.append(float(latency))

    n = len(pairs)
    denominators = dict(counts, n_scored=n,
                        coverage=(n / counts["n_labelled"]) if counts["n_labelled"] else None)
    if not n:
        return {"schema": "z0int.hermes_shadow_eval.v1", "question_id": question_id, "n": 0,
                "denominators": denominators}

    eps = 1e-12
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    log_loss = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in pairs) / n
    accuracy = sum((p >= 0.5) == bool(y) for p, y in pairs) / n
    positive_rate = sum(y for _, y in pairs) / n
    base_brier = sum((positive_rate - y) ** 2 for _, y in pairs) / n

    bins = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hi = lo + 0.2
        bucket = [(p, y) for p, y in pairs if lo <= p < hi or (hi >= 1.0 and p == 1.0)]
        if bucket:
            bins.append({
                "lo": lo, "hi": min(1.0, hi), "n": len(bucket),
                "mean_probability": sum(p for p, _ in bucket) / len(bucket),
                "observed_rate": sum(y for _, y in bucket) / len(bucket),
            })

    return {
        "schema": "z0int.hermes_shadow_eval.v1",
        "question_id": question_id,
        "backend": backend,
        "n": n,
        "denominators": denominators,
        "positive_rate": positive_rate,
        "brier": brier,
        "base_rate_brier": base_brier,
        "brier_improvement": base_brier - brier,
        "log_loss": log_loss,
        "accuracy_at_0_5": accuracy,
        "mean_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "calibration_bins": bins,
        "promotion_ready": False,
        "note": "Shadow evidence only; no runtime authority or promotion implied.",
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scored", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--question-id", default=QUESTION_ID)
    args = parser.parse_args()
    report = evaluate(read_jsonl(args.scored), question_id=args.question_id)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
