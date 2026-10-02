"""Portable JSON receipts and exact-input rechecks; never an authorization token."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .verify_core import BendVerifyError, capture_local_inputs, resolve_proof, runtime_identity


def stamp(result):
    return {**result, "receipt_id": uuid.uuid4().hex,
            "verified_at": datetime.now(timezone.utc).isoformat()}


def export_receipt(result, path):
    # Create-only: a verification command must not clobber a previous receipt.
    with Path(path).expanduser().open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")


def read_receipt(path):
    with Path(path).expanduser().open("rb") as source:
        data = source.read(128 * 1024 + 1)
    if len(data) > 128 * 1024:
        raise BendVerifyError("invalid_receipt", "Receipt exceeds 128 KiB")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise BendVerifyError("invalid_receipt", "Receipt is not valid JSON") from exc
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise BendVerifyError("invalid_receipt", "Unsupported receipt schema")
    fields = ("input_manifest_sha256", "proof_file", "receipt_id", "kernel_sha256_after")
    if any(not isinstance(result.get(key), str) or not result[key] for key in fields):
        raise BendVerifyError("invalid_receipt", "Receipt is missing input/kernel identity")
    if result.get("verification_scope") != "bend-emitted-book" or result.get("source_semantics_attested") is not False:
        raise BendVerifyError("invalid_receipt", "Receipt has an unsupported evidence scope")
    return result


def replay(service, receipt, project_dir):
    project, proof, _ = resolve_proof(project_dir, receipt["proof_file"])
    inputs = capture_local_inputs(project, proof, service.dependency_cache())
    if inputs["manifest_sha256"] != receipt["input_manifest_sha256"]:
        raise BendVerifyError("receipt_stale", "Project inputs differ from the recorded receipt; run a fresh verification")
    if runtime_identity(service.executable()) != receipt.get("runtime_identity"):
        raise BendVerifyError("receipt_stale", "Bend installation differs from the recorded receipt")
    result = service.verify(str(project), receipt["proof_file"])
    result["replay_of"] = receipt["receipt_id"]
    fields = ("input_manifest_sha256", "runtime_identity", "kernel_sha256_after", "execution_verdict")
    result["replay_differences"] = [key for key in fields if result.get(key) != receipt.get(key)]
    result["replay_match"] = not result["replay_differences"]
    if not result["replay_match"]:
        result["success"] = False
        result["verdict"] = "replay_mismatch"
    service.save_receipt(result)
    return result
