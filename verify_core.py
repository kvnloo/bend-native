"""Pure-stdlib Bend proof verification core used by the Hermes plugin and evals."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

_TIMEOUT_SECONDS = 30
_VERSION_TIMEOUT_SECONDS = 5
_OUTPUT_LIMIT = 8_000
_PASS_MARKER = "ALL PROOFS CHECK"
_MIN_BEND_VERSION = (2, 0, 32)
_BEND_ENV_DENY = ("BENDTT", "BEND_HUB", "BEND_LIB", "BEND_ORIGIN")
_VERSION_RE = re.compile(r"^bend\s+(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\s*$")

Runner = Callable[..., Any]
Which = Callable[[str], str | None]


class BendVerifyError(RuntimeError):
    """Expected adapter failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def bounded(value: str | bytes | None) -> str:
    """Bound captured process output while preserving both ends."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if len(value) <= _OUTPUT_LIMIT:
        return value
    marker = "\n... output truncated ...\n"
    room = (_OUTPUT_LIMIT - len(marker)) // 2
    return value[:room] + marker + value[-room:]


def clean_env(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return a verification environment without Bend trust-root overrides."""
    env = dict(os.environ if source is None else source)
    for key in _BEND_ENV_DENY:
        env.pop(key, None)
    env["BEND_NO_TELEMETRY"] = "1"
    return env


def resolve_proof(project: str, proof: str = "PROOF.bend") -> tuple[Path, Path, Path]:
    """Resolve a PROOF.bend that remains inside the requested project."""
    raw_project = str(project or "").strip()
    if not raw_project:
        raise BendVerifyError("invalid_input", "project_dir is required")

    project_dir = Path(raw_project).expanduser().resolve()
    if not project_dir.is_dir():
        raise BendVerifyError("invalid_input", f"project_dir is not a directory: {project_dir}")

    raw_proof = str(proof or "PROOF.bend").strip() or "PROOF.bend"
    proof_path = (project_dir / raw_proof).resolve()
    try:
        relative_proof = proof_path.relative_to(project_dir)
    except ValueError as exc:
        raise BendVerifyError("invalid_input", "proof_file must stay inside project_dir") from exc

    if proof_path.name != "PROOF.bend":
        raise BendVerifyError(
            "invalid_input",
            "proof_file must be named PROOF.bend so Bend enforces the adjacent LAWS.bend import",
        )
    if not proof_path.is_file():
        raise BendVerifyError("invalid_input", f"proof_file does not exist: {relative_proof}")

    return project_dir, proof_path, relative_proof


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Parse the documented bend X.Y.Z version line."""
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None:
        return None
    return tuple(int(value) for value in match.groups())


def version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(value) for value in version)


def query_version(bend: str, env: dict[str, str], *, runner: Runner = subprocess.run) -> tuple[int, int, int]:
    """Read and gate Bend's CLI version before trusting verdict semantics."""
    try:
        result = runner(
            [bend, "version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BendVerifyError(
            "unsupported_bend", f"could not query Bend version: {type(exc).__name__}: {exc}"
        ) from exc

    parsed = parse_version(result.stdout or "")
    if result.returncode != 0 or parsed is None:
        detail = (result.stdout or "").strip() or bounded(result.stderr) or "no output"
        raise BendVerifyError("unsupported_bend", f"could not parse Bend version: {detail}")
    if parsed < _MIN_BEND_VERSION:
        raise BendVerifyError(
            "unsupported_bend",
            f"Bend {version_text(parsed)} is too old; bend_verify requires >= "
            f"{version_text(_MIN_BEND_VERSION)} for --verdict semantics",
        )
    return parsed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(
    project_dir: str,
    proof_file: str = "PROOF.bend",
    *,
    which: Which = shutil.which,
    runner: Runner = subprocess.run,
    source_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run Bend's proven-kernel verdict and return structured evidence."""
    project, proof, relative = resolve_proof(project_dir, proof_file)
    bend = which("bend")
    if bend is None:
        raise BendVerifyError("bend_not_found", "Bend CLI not found on PATH")

    env = clean_env(source_env)
    version = query_version(bend, env, runner=runner)
    version_string = version_text(version)
    proof_sha_before = sha256_file(proof)
    started = time.monotonic()

    try:
        result = runner(
            [bend, relative.as_posix(), "--verdict"],
            cwd=str(project),
            env=env,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "verdict": "timeout",
            "exit_code": None,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "timeout_seconds": _TIMEOUT_SECONDS,
            "proof_file": relative.as_posix(),
            "proof_sha256": proof_sha_before,
            "proof_changed_during_verify": False,
            "bend_version": version_string,
            "bend_path": bend,
            "stdout": bounded(exc.stdout),
            "stderr": bounded(exc.stderr),
        }
    except OSError as exc:
        raise BendVerifyError(
            "bend_exec_failed", f"failed to execute Bend: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        proof_sha_after = sha256_file(proof)
    except OSError:
        proof_sha_after = None
    changed = proof_sha_after != proof_sha_before
    raw_stdout = result.stdout or ""
    exact_pass = result.returncode == 0 and raw_stdout.strip() == _PASS_MARKER

    if changed:
        verdict = "unstable"
    elif exact_pass:
        verdict = "pass"
    elif result.returncode == 0:
        verdict = "indeterminate"
    else:
        verdict = "fail"

    return {
        "success": verdict == "pass",
        "verdict": verdict,
        "exit_code": result.returncode,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "proof_file": relative.as_posix(),
        "proof_sha256": proof_sha_after or proof_sha_before,
        "proof_changed_during_verify": changed,
        "bend_version": version_string,
        "bend_path": bend,
        "stdout": bounded(result.stdout),
        "stderr": bounded(result.stderr),
    }
