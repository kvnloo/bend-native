"""A private BendTT kernel per active Hermes profile, cleaned up on plugin unload."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading

from .verify_core import (
    BendVerifyError, clean_env, kernel_cache_identity, runtime_identity, verify,
)


class KernelSession:
    def __init__(self):
        self._lock = threading.RLock()
        self._home = None
        self._bend = None
        self._identity = None
        self._kernel = None
        self._kernel_sha = None
        self._closed = False

    def verify(self, bend: str, project: str, proof: str, dependency_cache=None):
        # Serialize within a profile; other profiles own independent sessions.
        with self._lock:
            if self._closed:
                raise BendVerifyError("plugin_unloaded", "Bend plugin was unloaded; start a new verification after enabling it")
            identity = runtime_identity(bend)
            if self._identity is not None and (bend != self._bend or identity != self._identity):
                raise BendVerifyError("bend_integrity", "Bend installation changed; restart Hermes")
            if self._kernel is not None:
                result = verify(project, proof, which=lambda _: bend,
                                kernel_override=self._kernel,
                                kernel_expected_sha256=self._kernel_sha,
                                runtime_expected=self._identity, dependency_cache=dependency_cache)
                result["kernel_strategy"] = "session-pinned"
                return result

            home = tempfile.TemporaryDirectory(prefix="hermes-bend-kernel-")
            env = clean_env()
            env["ELAN_HOME"] = env.get("ELAN_HOME") or str(Path.home() / ".elan")
            env["HOME"] = home.name
            try:
                result = verify(project, proof, which=lambda _: bend, source_env=env,
                                runtime_expected=identity, dependency_cache=dependency_cache)
                kernel = kernel_cache_identity(bend, env)
                if result["verdict"] in {"timeout", "unstable"} or not kernel["sha256"]:
                    home.cleanup()
                    result["kernel_strategy"] = "session-uninitialized"
                    if result["success"]:
                        raise BendVerifyError("kernel_integrity", "Bend did not produce an identifiable kernel")
                    return result
                self._home, self._bend, self._identity = home, bend, identity
                self._kernel, self._kernel_sha = kernel["path"], kernel["sha256"]
                result["kernel_strategy"] = "session-bootstrap"
                return result
            except BaseException:
                home.cleanup()
                raise

    def close(self):
        with self._lock:
            self._closed = True
            if self._home is not None:
                self._home.cleanup()
            self._home = self._bend = self._identity = self._kernel = self._kernel_sha = None
