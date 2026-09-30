"""Minimal Bend proof-verification tool."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

_TIMEOUT_SECONDS = 30
_OUTPUT_LIMIT = 8_000
_PASS_MARKER = "ALL PROOFS CHECK"

BEND_VERIFY_SCHEMA = {
    "name": "bend_verify",
    "description": (
        "Verify a Bend proof file with Bend's proven BendTT kernel. "
        "Use this for machine-checkable proof evidence; it does not edit files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Absolute or relative path to the Bend project directory.",
            },
            "proof_file": {
                "type": "string",
                "description": "Proof file inside project_dir. Defaults to PROOF.bend.",
            },
        },
        "required": ["project_dir"],
    },
}


def check_bend_available() -> bool:
    """Return whether the Bend CLI is installed."""
    return shutil.which("bend") is not None


def _bounded(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if len(value) <= _OUTPUT_LIMIT:
        return value
    marker = "\n... output truncated ...\n"
    room = (_OUTPUT_LIMIT - len(marker)) // 2
    return value[:room] + marker + value[-room:]


def _resolve_proof(args: dict[str, Any]) -> tuple[Path, Path, Path]:
    raw_project = str(args.get("project_dir") or "").strip()
    if not raw_project:
        raise ValueError("project_dir is required")

    project_dir = Path(raw_project).expanduser().resolve()
    if not project_dir.is_dir():
        raise ValueError(f"project_dir is not a directory: {project_dir}")

    raw_proof = str(args.get("proof_file") or "PROOF.bend").strip()
    proof_path = (project_dir / raw_proof).resolve()
    try:
        relative_proof = proof_path.relative_to(project_dir)
    except ValueError as exc:
        raise ValueError("proof_file must stay inside project_dir") from exc

    if proof_path.suffix != ".bend":
        raise ValueError("proof_file must be a .bend file")
    if not proof_path.is_file():
        raise ValueError(f"proof_file does not exist: {relative_proof}")

    return project_dir, proof_path, relative_proof


def handle_bend_verify(args: dict[str, Any]) -> str:
    """Run Bend's proven-kernel verdict and return bounded structured evidence."""
    try:
        project_dir, _proof_path, relative_proof = _resolve_proof(args)
    except ValueError as exc:
        return tool_error(str(exc))

    bend = shutil.which("bend")
    if bend is None:
        return tool_error("Bend CLI not found on PATH")

    env = os.environ.copy()
    env["BEND_NO_TELEMETRY"] = "1"
    command = [bend, relative_proof.as_posix(), "--verdict"]

    try:
        result = subprocess.run(
            command,
            cwd=str(project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return tool_result(
            {
                "success": False,
                "verdict": "timeout",
                "timeout_seconds": _TIMEOUT_SECONDS,
                "proof_file": relative_proof.as_posix(),
                "stdout": _bounded(exc.stdout),
                "stderr": _bounded(exc.stderr),
            }
        )

    stdout = _bounded(result.stdout)
    stderr = _bounded(result.stderr)
    passed = result.returncode == 0 and _PASS_MARKER in stdout

    return tool_result(
        {
            "success": passed,
            "verdict": "pass" if passed else "fail",
            "exit_code": result.returncode,
            "proof_file": relative_proof.as_posix(),
            "stdout": stdout,
            "stderr": stderr,
        }
    )
