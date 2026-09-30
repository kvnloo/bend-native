"""Process-scoped trusted BendTT kernel for the downstream Hermes plugin."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any

from .verify_core import (
    BendVerifyError,
    clean_env,
    file_identity_sha256,
    kernel_cache_identity,
    verify,
)


@dataclass
class _KernelSession:
    tempdir: tempfile.TemporaryDirectory
    bend_path: str
    bend_sha256: str
    kernel_path: str
    kernel_sha256: str
    kernel_source_sha256: str | None


_lock = threading.RLock()
_verify_slots = threading.BoundedSemaphore(4)
_state: _KernelSession | None = None


def _current_bend() -> tuple[str, str]:
    bend = shutil.which("bend")
    if bend is None:
        raise BendVerifyError("bend_not_found", "Bend CLI not found on PATH")
    bend = str(Path(bend).resolve())
    digest = file_identity_sha256(bend)
    if digest is None:
        raise BendVerifyError("bend_integrity", f"Bend CLI is not a stable regular file: {bend}")
    return bend, digest


def _bootstrap_env(home: str) -> dict[str, str]:
    env = dict(os.environ)
    # Changing HOME would otherwise also hide elan's installed Lean toolchain.
    env["ELAN_HOME"] = os.environ.get("ELAN_HOME") or str(Path.home() / ".elan")
    env["HOME"] = home
    return env


def _trusted_state(bend: str, bend_sha256: str) -> _KernelSession | None:
    state = _state
    if state is None:
        return None
    if state.bend_path != bend or state.bend_sha256 != bend_sha256:
        raise BendVerifyError(
            "bend_integrity",
            "Bend binary changed after this Hermes process pinned its BendTT kernel; "
            "restart Hermes before trusting another verifier binary",
        )
    actual_kernel = file_identity_sha256(state.kernel_path)
    if actual_kernel != state.kernel_sha256:
        raise BendVerifyError(
            "kernel_integrity",
            "the process-pinned BendTT kernel changed after bootstrap",
        )
    return state


def verify_with_session_kernel(project_dir: str, proof_file: str = "PROOF.bend") -> dict[str, Any]:
    """Build a private kernel once, then pin its exact path and hash for this process."""
    global _state

    bend, bend_sha256 = _current_bend()

    # Fast path: after bootstrap, verification calls can run concurrently.
    with _lock:
        state = _trusted_state(bend, bend_sha256)
    if state is not None:
        with _verify_slots:
            result = verify(
                project_dir,
                proof_file,
                which=lambda _name: state.bend_path,
                kernel_override=state.kernel_path,
                kernel_expected_sha256=state.kernel_sha256,
            )
        result["kernel_strategy"] = "session-pinned"
        result["session_kernel_sha256"] = state.kernel_sha256
        result["session_kernel_source_sha256"] = state.kernel_source_sha256
        result["scheduler_limit"] = 4
        return result

    # Only one caller may pay the cold Lean/BendTT compile.
    with _lock:
        state = _trusted_state(bend, bend_sha256)
        if state is not None:
            kernel_path = state.kernel_path
            kernel_sha = state.kernel_sha256
            kernel_source_sha = state.kernel_source_sha256
        else:
            tempdir = tempfile.TemporaryDirectory(prefix="hermes-bend-kernel-")
            env = _bootstrap_env(tempdir.name)
            try:
                result = verify(
                    project_dir,
                    proof_file,
                    which=lambda _name: bend,
                    source_env=env,
                )
                identity = kernel_cache_identity(bend, clean_env(env))
                if result.get("execution_verdict") == "timeout" or identity.get("sha256") is None:
                    tempdir.cleanup()
                    result["kernel_strategy"] = "session-uninitialized"
                    return result
                _state = _KernelSession(
                    tempdir=tempdir,
                    bend_path=bend,
                    bend_sha256=bend_sha256,
                    kernel_path=str(identity["path"]),
                    kernel_sha256=str(identity["sha256"]),
                    kernel_source_sha256=identity.get("source_sha256"),
                )
                result["kernel_strategy"] = "session-bootstrap"
                result["session_kernel_sha256"] = _state.kernel_sha256
                result["session_kernel_source_sha256"] = _state.kernel_source_sha256
                result["scheduler_limit"] = 4
                return result
            except Exception:
                tempdir.cleanup()
                raise

    # Another caller completed bootstrap while this caller waited.
    with _verify_slots:
        result = verify(
            project_dir,
            proof_file,
            which=lambda _name: bend,
            kernel_override=kernel_path,
            kernel_expected_sha256=kernel_sha,
        )
    result["kernel_strategy"] = "session-pinned"
    result["session_kernel_sha256"] = kernel_sha
    result["session_kernel_source_sha256"] = kernel_source_sha
    result["scheduler_limit"] = 4
    return result


def _reset_session_kernel_for_tests() -> None:
    global _state
    with _lock:
        state, _state = _state, None
        if state is not None:
            state.tempdir.cleanup()
