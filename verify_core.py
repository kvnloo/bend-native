"""Pure-stdlib Bend proof verification core used by the Hermes plugin and evals."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

_TIMEOUT_SECONDS = 30
_KERNEL_BOOTSTRAP_TIMEOUT_SECONDS = 120
_VERSION_TIMEOUT_SECONDS = 5
_OUTPUT_LIMIT = 8_000
_MAX_INPUT_FILES = 2_048
_MAX_INPUT_BYTES = 64 * 1024 * 1024
_PASS_MARKER = "ALL PROOFS CHECK"
_MIN_BEND_VERSION = (2, 0, 32)
_BEND_ENV_DENY = ("BENDTT", "BEND_HUB", "BEND_LIB", "BEND_ORIGIN")
_VERSION_RE = re.compile(r"^bend\s+(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\s*$")
_IMPORT_RE = re.compile(
    r"^import\s+(\S+)(?:\s+as\s+([A-Za-z_]\w*))?\s*(?:#.*)?$"
)
_HASH_IMPORT_RE = re.compile(r"^0x[0-9a-f]+/")
_NAMED_IMPORT_RE = re.compile(
    r"^[a-z][a-z0-9-]{0,63}@(0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){3}/"
)

Runner = Callable[..., Any]
Which = Callable[[str], str | None]


class BendVerifyError(RuntimeError):
    """Expected adapter failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def run_process(
    command: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a child with bounded lifetime, reaping its process tree on POSIX."""
    if os.name != "posix":
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            check=check,
        )

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout if stdout is not None else exc.output,
            stderr=stderr if stderr is not None else exc.stderr,
        ) from exc

    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode, command, output=stdout, stderr=stderr
        )
    return result


def bounded(value: str | bytes | None) -> str:
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
    """Remove project-selectable Bend trust roots from a verification child."""
    env = dict(os.environ if source is None else source)
    for key in _BEND_ENV_DENY:
        env.pop(key, None)
    env["BEND_NO_TELEMETRY"] = "1"
    return env


def _relative_inside(root: Path, candidate: Path, *, what: str) -> Path:
    try:
        return candidate.relative_to(root)
    except ValueError as exc:
        raise BendVerifyError("invalid_input", f"{what} must stay inside project_dir") from exc


def _reject_symlink_components(root: Path, candidate: Path, *, what: str) -> None:
    relative = _relative_inside(root, candidate, what=what)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BendVerifyError(
                "unsupported_input",
                f"{what} may not traverse a symlink: {relative.as_posix()}",
            )


def resolve_proof(project: str, proof: str = "PROOF.bend") -> tuple[Path, Path, Path]:
    raw_project = str(project or "").strip()
    if not raw_project:
        raise BendVerifyError("invalid_input", "project_dir is required")

    project_dir = Path(raw_project).expanduser().resolve()
    if not project_dir.is_dir():
        raise BendVerifyError("invalid_input", f"project_dir is not a directory: {project_dir}")

    raw_proof = str(proof or "PROOF.bend").strip() or "PROOF.bend"
    if Path(raw_proof).is_absolute():
        raise BendVerifyError("invalid_input", "proof_file must be relative to project_dir")

    lexical = Path(os.path.abspath(project_dir / raw_proof))
    relative_proof = _relative_inside(project_dir, lexical, what="proof_file")
    _reject_symlink_components(project_dir, lexical, what="proof_file")

    if lexical.name != "PROOF.bend":
        raise BendVerifyError(
            "invalid_input",
            "proof_file must be named PROOF.bend so Bend enforces the adjacent LAWS.bend import",
        )
    if not lexical.is_file():
        raise BendVerifyError("invalid_input", f"proof_file does not exist: {relative_proof}")

    return project_dir, lexical, relative_proof


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None:
        return None
    return tuple(int(value) for value in match.groups())


def version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(value) for value in version)


def query_version(bend: str, env: dict[str, str], *, runner: Runner = run_process) -> tuple[int, int, int]:
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=32)
def _cached_file_sha256(path: str, size: int, mtime_ns: int, ctime_ns: int, inode: int) -> str:
    del size, mtime_ns, ctime_ns, inode
    return sha256_file(Path(path))


def file_identity_sha256(path: str) -> str | None:
    try:
        real = Path(path).resolve()
        stat = real.stat()
        if not real.is_file():
            return None
        return _cached_file_sha256(
            str(real), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
        )
    except OSError:
        return None


def kernel_cache_identity(bend: str, env: dict[str, str]) -> dict[str, Any]:
    """Describe Bend's expected cached BendTT kernel without executing it."""
    try:
        bend_real = Path(bend).resolve()
        source = bend_real.parent.parent / "bend2" / "bendtt.lean"
        if not source.is_file():
            return {"state": "unknown", "path": None, "source_sha256": None, "sha256": None}
        source_bytes = source.read_bytes()
        source_sha256 = sha256_bytes(source_bytes)
        source_key = source_sha256[:16]
        home = Path(env.get("HOME") or str(Path.home())).expanduser()
        kernel = home / ".bend" / "bendtt" / source_key / "bendtt"
        kernel_sha256 = file_identity_sha256(str(kernel))
        return {
            "state": "warm" if kernel_sha256 is not None else "cold",
            "path": str(kernel),
            "source_sha256": source_sha256,
            "sha256": kernel_sha256,
        }
    except OSError:
        return {"state": "unknown", "path": None, "source_sha256": None, "sha256": None}


def _read_stable(path: Path) -> bytes:
    """Read one local input only if metadata stays stable across the read."""
    for _ in range(3):
        try:
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
        except OSError as exc:
            raise BendVerifyError(
                "input_capture_failed", f"could not read Bend input {path}: {exc}"
            ) from exc
        signature_before = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
        )
        signature_after = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        )
        if signature_before == signature_after and len(data) == after.st_size:
            return data
    raise BendVerifyError("input_unstable", f"Bend input changed while being captured: {path}")


def _local_imports(text: str) -> list[str]:
    """Return local .bend imports from Bend's import prefix; hub imports stay external."""
    imports: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line == "" or line.startswith("#"):
            continue
        if not line.startswith("import"):
            break
        match = _IMPORT_RE.fullmatch(line)
        if match is None:
            continue  # Bend itself will reject the malformed line in the snapshot.
        target, alias = match.groups()
        if alias is None:
            continue  # only valid alias-free import is Base; Bend validates that.
        if _HASH_IMPORT_RE.match(target) or _NAMED_IMPORT_RE.match(target):
            continue
        if target.endswith(".bend"):
            imports.append(target)
    return imports


def _manifest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative, data in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
        digest.update(b"\n")
    return digest.hexdigest()


def _local_candidate(project: Path, parent: Path, raw: str) -> Path:
    if Path(raw).is_absolute():
        raise BendVerifyError(
            "unsupported_import",
            f"absolute local Bend imports are not snapshot-safe: {raw}",
        )
    candidate = Path(os.path.abspath(parent / raw))
    _relative_inside(project, candidate, what=f"local import {raw}")
    _reject_symlink_components(project, candidate, what=f"local import {raw}")
    if not candidate.is_file():
        raise BendVerifyError("input_capture_failed", f"local Bend import does not exist: {raw}")
    return candidate


def capture_local_inputs(project: Path, proof: Path) -> dict[str, Any]:
    """Capture the local Bend import closure plus adjacent LAWS.bend.

    The adjacent law file is included even when PROOF.bend forgot to import it,
    preserving Bend's own special missing-import invariant inside the snapshot.
    """
    files: dict[str, bytes] = {}
    queued = [proof]
    adjacent_laws = proof.parent / "LAWS.bend"
    if adjacent_laws.exists():
        _reject_symlink_components(project, adjacent_laws, what="LAWS.bend")
        queued.append(adjacent_laws)

    total_bytes = 0
    while queued:
        path = queued.pop()
        relative = _relative_inside(project, path, what="Bend input").as_posix()
        if relative in files:
            continue
        if len(files) >= _MAX_INPUT_FILES:
            raise BendVerifyError(
                "input_too_large", f"Bend local import closure exceeds {_MAX_INPUT_FILES} files"
            )
        data = _read_stable(path)
        total_bytes += len(data)
        if total_bytes > _MAX_INPUT_BYTES:
            raise BendVerifyError(
                "input_too_large",
                f"Bend local import closure exceeds {_MAX_INPUT_BYTES} bytes",
            )
        files[relative] = data
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BendVerifyError("invalid_input", f"Bend input is not UTF-8: {relative}") from exc
        for raw_import in _local_imports(text):
            queued.append(_local_candidate(project, path.parent, raw_import))

    proof_relative = _relative_inside(project, proof, what="proof_file").as_posix()
    return {
        "files": files,
        "manifest_sha256": _manifest(files),
        "file_count": len(files),
        "byte_count": total_bytes,
        "proof_sha256": sha256_bytes(files[proof_relative]),
    }


def _materialize(files: dict[str, bytes], root: Path) -> None:
    for relative, data in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _directory_manifest(root: Path) -> tuple[str, int]:
    files: dict[str, bytes] = {}
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    return _manifest(files), len(files)


def _source_state(project: Path, proof: Path, initial: dict[str, Any]) -> tuple[bool, bool | None, str | None]:
    try:
        current = capture_local_inputs(project, proof)
    except BendVerifyError as exc:
        return True, None, f"{exc.code}: {exc}"
    proof_relative = _relative_inside(project, proof, what="proof_file").as_posix()
    current_proof = current["files"].get(proof_relative)
    initial_proof = initial["files"].get(proof_relative)
    proof_changed = current_proof != initial_proof
    return current["manifest_sha256"] != initial["manifest_sha256"], proof_changed, None


def verify(
    project_dir: str,
    proof_file: str = "PROOF.bend",
    *,
    which: Which = shutil.which,
    runner: Runner = run_process,
    source_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verify an immutable local-input snapshot with an isolated Bend package cache."""
    project, proof, relative = resolve_proof(project_dir, proof_file)
    captured = capture_local_inputs(project, proof)

    bend = which("bend")
    if bend is None:
        raise BendVerifyError("bend_not_found", "Bend CLI not found on PATH")

    env = clean_env(source_env)
    version = query_version(bend, env, runner=runner)
    version_string = version_text(version)
    bend_sha256 = file_identity_sha256(bend)
    kernel_before = kernel_cache_identity(bend, env)
    verdict_timeout = (
        _KERNEL_BOOTSTRAP_TIMEOUT_SECONDS
        if kernel_before["state"] == "cold"
        else _TIMEOUT_SECONDS
    )
    started = time.monotonic()

    timed_out = False
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    bend_lib_manifest = _manifest({})
    bend_lib_file_count = 0

    with tempfile.TemporaryDirectory(prefix="hermes-bend-verify-") as raw_tmp:
        temp_root = Path(raw_tmp)
        snapshot = temp_root / "project"
        bend_lib = temp_root / "bend-lib"
        snapshot.mkdir()
        bend_lib.mkdir()
        _materialize(captured["files"], snapshot)

        child_env = dict(env)
        child_env["BEND_LIB"] = str(bend_lib)
        command = [bend, relative.as_posix(), "--verdict"]
        try:
            result = runner(
                command,
                cwd=str(snapshot),
                env=child_env,
                capture_output=True,
                text=True,
                timeout=verdict_timeout,
                check=False,
            )
            exit_code = result.returncode
            stdout = bounded(result.stdout)
            stderr = bounded(result.stderr)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = bounded(exc.stdout)
            stderr = bounded(exc.stderr)
        except OSError as exc:
            raise BendVerifyError(
                "bend_exec_failed", f"failed to execute Bend: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            bend_lib_manifest, bend_lib_file_count = _directory_manifest(bend_lib)

    kernel_after = kernel_cache_identity(bend, env)
    kernel_changed = (
        kernel_before["state"] == "warm"
        and kernel_before["sha256"] is not None
        and kernel_after["sha256"] != kernel_before["sha256"]
    )
    source_changed, proof_changed, source_recheck_error = _source_state(project, proof, captured)
    raw_exact_pass = (
        not timed_out
        and exit_code == 0
        and stdout.strip() == _PASS_MARKER
    )
    if timed_out:
        execution_verdict = "timeout"
    elif raw_exact_pass:
        execution_verdict = "pass"
    elif exit_code == 0:
        execution_verdict = "indeterminate"
    else:
        execution_verdict = "fail"

    verdict = "unstable" if source_changed or kernel_changed else execution_verdict
    result_payload = {
        "success": verdict == "pass",
        "verdict": verdict,
        "execution_verdict": execution_verdict,
        "exit_code": exit_code,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "proof_file": relative.as_posix(),
        "proof_sha256": captured["proof_sha256"],
        "proof_changed_during_verify": proof_changed,
        "source_changed_during_verify": source_changed,
        "input_manifest_sha256": captured["manifest_sha256"],
        "input_file_count": captured["file_count"],
        "input_byte_count": captured["byte_count"],
        "cache_mode": "isolated",
        "kernel_cache_state": kernel_before["state"],
        "kernel_source_sha256": kernel_before["source_sha256"],
        "kernel_sha256_before": kernel_before["sha256"],
        "kernel_sha256_after": kernel_after["sha256"],
        "kernel_changed_during_verify": kernel_changed,
        "verdict_timeout_seconds": verdict_timeout,
        "bend_lib_manifest_sha256": bend_lib_manifest,
        "bend_lib_file_count": bend_lib_file_count,
        "bend_version": version_string,
        "bend_path": bend,
        "bend_sha256": bend_sha256,
        "stdout": stdout,
        "stderr": stderr,
    }
    if timed_out:
        result_payload["timeout_seconds"] = verdict_timeout
    if source_recheck_error is not None:
        result_payload["source_recheck_error"] = source_recheck_error
    return result_payload
