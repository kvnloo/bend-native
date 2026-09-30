"""Hermes tool surface for Bend proof verification."""
from __future__ import annotations

import shutil
from typing import Any

from tools.registry import tool_error, tool_result
from .verify_core import BendVerifyError, verify

BEND_VERIFY_SCHEMA = {
    "name": "bend_verify",
    "description": (
        "Verify a project's PROOF.bend with Bend's proven BendTT kernel. "
        "Returns machine-checkable pass/fail evidence and does not edit files."
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
                "description": (
                    "Path to a file named PROOF.bend inside project_dir. "
                    "Defaults to PROOF.bend."
                ),
            },
        },
        "required": ["project_dir"],
    },
}


def check_bend_available() -> bool:
    return shutil.which("bend") is not None


def handle_bend_verify(args: dict[str, Any]) -> str:
    try:
        result = verify(
            project_dir=str(args.get("project_dir") or ""),
            proof_file=str(args.get("proof_file") or "PROOF.bend"),
        )
    except BendVerifyError as exc:
        return tool_error(str(exc), code=exc.code)
    return tool_result(result)
