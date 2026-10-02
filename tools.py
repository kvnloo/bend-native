"""Hermes's native tool and CLI share the same verifier service."""
from __future__ import annotations

from pathlib import Path
import shutil
import threading

from hermes_constants import get_hermes_home
from tools.registry import tool_error, tool_result

from .session_kernel import KernelSession
from .receipts import stamp
from .verify_core import BendVerifyError, clean_env, query_version, runtime_identity, version_text

BEND_VERIFY_SCHEMA = {
    "name": "bend_verify",
    "description": (
        "Check a project's PROOF.bend using Bend --verdict and return a snapshot receipt. "
        "Paths refer to the Hermes host. A PASS checks Bend's emitted book; it does not "
        "attest compiler/source equivalence or complete application correctness. "
        "Read the bend:workflow skill for the edit/prove/check workflow."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {"type": "string", "description": "Absolute project directory on the Hermes host."},
            "proof_file": {"type": "string", "description": "Relative path named PROOF.bend (default PROOF.bend)."},
        },
        "required": ["project_dir"],
        "additionalProperties": False,
    },
}


class BendService:
    def __init__(self, ctx):
        self.ctx = ctx
        self._lock = threading.RLock()
        self._sessions = {}

    def executable(self):
        configured = self.ctx.get_config("executable", "bend")
        if not isinstance(configured, str) or not configured.strip():
            raise BendVerifyError("invalid_config", "Bend executable must be a path or command name")
        path = shutil.which(str(Path(configured).expanduser()))
        if path is None:
            raise BendVerifyError("bend_not_found", "Bend CLI not found; install Bend and run hermes bend doctor")
        return str(Path(path).resolve())

    def dependency_cache(self):
        configured = self.ctx.get_config("dependency_cache", "")
        if not isinstance(configured, str):
            raise BendVerifyError("invalid_config", "dependency_cache must be a directory path")
        return str(Path(configured).expanduser().resolve()) if configured else str(Path.home() / ".bend/lib")

    def doctor(self):
        bend = self.executable()
        version = query_version(bend, clean_env())
        return {"available": True, "bend_path": bend, "bend_version": version_text(version),
                "runtime_identity": runtime_identity(bend),
                "verification_scope": "bend-emitted-book", "source_semantics_attested": False,
                "kernel_ready": False,
                "next": "Run hermes bend verify PROJECT to build and exercise the private kernel. Lean 4.34.0 must be installed."}

    def verify(self, project, proof="PROOF.bend"):
        if not isinstance(project, str) or not project.strip():
            raise BendVerifyError("invalid_input", "project_dir is required")
        if not isinstance(proof, str):
            raise BendVerifyError("invalid_input", "proof_file must be a string")
        bend = self.executable()
        scope = str(get_hermes_home())
        with self._lock:
            session = self._sessions.setdefault(scope, KernelSession())
        result = stamp(session.verify(bend, project, proof, dependency_cache=self.dependency_cache()))
        self.save_receipt(result)
        return result

    def save_receipt(self, result):
        try:
            result["receipt_saved"] = True
            self.ctx.state.set("last_receipt", result)
        except (OSError, RuntimeError, ValueError) as exc:
            result["receipt_saved"] = False
            result["receipt_storage_error"] = str(exc)

    def handle(self, args, **kwargs):
        try:
            project = args.get("project_dir")
            if not isinstance(project, str) or not Path(project).expanduser().is_absolute():
                raise BendVerifyError("invalid_input", "project_dir must be an absolute path on the Hermes host")
            return tool_result(self.verify(project, args.get("proof_file", "PROOF.bend")))
        except (BendVerifyError, OSError) as exc:
            return tool_error(str(exc), code=getattr(exc, "code", "io_error"), success=False)

    def close(self):
        with self._lock:
            for session in self._sessions.values():
                session.close()
            self._sessions.clear()
