"""Observer-only Hermes -> z0intelligence event bridge.

This lab plugin deliberately records metadata, not prompt/tool content.  It is a
measurement substrate for downstream z0 experiments; every callback returns
None and every failure is swallowed so Hermes behavior is unchanged.

Rows are encoded on the calling thread and appended by one background writer, so a hook
callback never waits on storage.  ``pre_tool_call`` fails closed when a callback outlives
the host timeout and ``subagent_stop`` runs on the caller thread; a stalled disk must not
become a blocked tool or a hung parent.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home

PLUGIN_ID = "z0-hermes-observer"
SCHEMA = "z0int.hermes_observer_event.v1"

_MAX_PENDING = 4096        # rows buffered while the writer is busy or storage is stalled
_EXIT_FLUSH_SECONDS = 2.0  # longest a normal interpreter exit waits for buffered rows

# Delivery state, all guarded by _STATE.  It is never held across I/O, so a hook callback
# can wait on a few deque operations but never on storage.
_STATE = threading.Condition()
_pending: deque[tuple[Path, str]] = deque()  # (spool path, encoded row)
_inflight = 0   # rows the writer has taken and not finished with
_dropped = 0    # rows lost since the last observer_rows_dropped marker was written
_worker: threading.Thread | None = None
_exit_hook = False

_HOOKS = (
    "on_session_start",
    "pre_llm_call",
    "pre_api_request",
    "post_api_request",
    "api_request_error",
    "pre_auxiliary_call",
    "post_auxiliary_call",
    "pre_tool_call",
    "post_tool_call",
    "subagent_stop",
    "on_session_end",
)

_SCALARS = (
    "task_id", "turn_id", "api_request_id", "session_id", "tool_call_id",
    "platform", "surface", "model", "provider", "api_mode", "aux_task",
    "api_call_count", "retry_count", "max_retries", "message_count",
    "tool_count", "approx_input_tokens", "request_char_count", "max_tokens",
    "api_duration", "duration_ms", "started_at", "ended_at", "status_code",
    "retryable", "reason", "finish_reason", "response_model",
    "assistant_content_chars", "assistant_tool_call_count", "streaming",
    "completed", "failed", "interrupted", "turn_exit_reason",
    "child_role", "child_status", "telemetry_schema_version",
)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _event_path() -> Path:
    """Spool file of the active profile: ``<hermes home>/plugin-data/<id>/events.jsonl``.

    Same layout as ``plugins.plugin_storage.plugin_data_dir`` but computed without touching
    the filesystem, so it follows whichever profile is bound to the calling context. A
    profile whose storage cannot be created loses its rows; it never falls back to another
    profile's home.
    """
    override = os.environ.get("Z0INT_HERMES_EVENT_PATH")
    if override:
        return Path(override).expanduser()
    return get_hermes_home() / "plugin-data" / PLUGIN_ID / "events.jsonl"


def _coordinates(event: str, payload: Mapping[str, Any]) -> tuple[Any, Any]:
    """(session_id, turn_id) of the turn an event belongs to.

    The host announces ``subagent_stop`` from the parent's side, with ``parent_session_id`` /
    ``parent_turn_id`` instead of ``session_id`` / ``turn_id``.  Keying it by the parent's
    coordinates joins it to the rest of the turn that delegated.
    """
    session_id, turn_id = payload.get("session_id"), payload.get("turn_id")
    if event == "subagent_stop":
        session_id = session_id or payload.get("parent_session_id")
        turn_id = turn_id or payload.get("parent_turn_id")
    return session_id, turn_id


def _trace_id(event: str, payload: Mapping[str, Any]) -> str | None:
    explicit = payload.get("trace_id")
    if explicit:
        return str(explicit)
    session_id, turn_id = (str(part or "") for part in _coordinates(event, payload))
    if session_id and turn_id:
        return hashlib.sha256((session_id + "\0" + turn_id).encode("utf-8")).hexdigest()
    return session_id or str(payload.get("task_id") or "") or None


def _usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int | float] = {}
    for key, raw in value.items():
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            out[str(key)] = raw
    return out


def _tool_shape(payload: Mapping[str, Any]) -> dict[str, Any]:
    args = payload.get("args")
    result: dict[str, Any] = {
        "tool_name": str(payload.get("tool_name") or ""),
        "arg_keys": sorted(str(k) for k in args) if isinstance(args, Mapping) else [],
    }
    # Never persist raw args/results in v0.  The host already exposes status and
    # bounded error metadata, which are sufficient for the first outcome joins.
    for key in ("status", "error_type", "duration_ms"):
        if payload.get(key) is not None:
            result[key] = payload[key]
    return result


def _subagent_shape(payload: Mapping[str, Any]) -> dict[str, Any]:
    history = payload.get("tool_call_history")
    return {
        "parent_session_id": payload.get("parent_session_id"),
        "parent_turn_id": payload.get("parent_turn_id"),
        "child_session_id": payload.get("child_session_id"),
        "child_role": payload.get("child_role"),
        "child_status": payload.get("child_status"),
        "duration_ms": payload.get("duration_ms"),
        "tool_calls": len(history) if isinstance(history, list) else 0,
    }


def _row(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    session_id, turn_id = _coordinates(event, payload)
    identity = {
        "harness_id": "hermes",
        "session_id": session_id,
        "task_id": payload.get("task_id"),
        "turn_id": turn_id,
        "trace_id": _trace_id(event, payload),
        "api_request_id": payload.get("api_request_id"),
        "tool_call_id": payload.get("tool_call_id"),
    }
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "event": event,
        "observed_at": time.time(),
        "identity": identity,
        "fields": {key: payload[key] for key in _SCALARS if payload.get(key) is not None},
    }
    usage = _usage(payload.get("usage"))
    if usage:
        data["usage"] = usage
    if event in {"pre_tool_call", "post_tool_call"}:
        data["tool"] = _tool_shape(payload)
    if event == "subagent_stop":
        data["subagent"] = _subagent_shape(payload)
    if _truthy("Z0INT_HERMES_CAPTURE_SANITIZED_CONTENT"):
        # Explicit opt-in only. Hermes sanitizes these envelopes for secret-key
        # fields, but they may still contain user text, so default is metadata-only.
        if payload.get("request") is not None:
            data["request"] = payload["request"]
        if payload.get("response") is not None:
            data["response"] = payload["response"]
    return data


def _encode(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _dropped_row(count: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "event": "observer_rows_dropped",
        "observed_at": time.time(),
        "identity": {"harness_id": "hermes"},
        "fields": {"dropped_rows": count},
    }


def _append(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for line in lines:
            stream.write(line)
            stream.flush()  # one write per row keeps rows from concurrent appenders whole


def _write_batch(batch: list[tuple[Path, str]], lost: int) -> int:
    """Append *batch*, led by a drop marker for *lost* earlier rows; return rows left unwritten."""
    groups: dict[Path, list[str]] = {}
    for path, line in batch:
        groups.setdefault(path, []).append(line)
    marker_path = batch[0][0]
    unwritten = 0
    for path, lines in groups.items():
        carried = lost if path == marker_path else 0
        if carried:
            lines = [_encode(_dropped_row(carried)), *lines]
        try:
            _append(path, lines)
        except Exception:
            unwritten += (len(lines) - 1 + carried) if carried else len(lines)
    return unwritten


def _drain() -> None:
    global _inflight, _dropped
    while True:
        with _STATE:
            while not _pending:
                _STATE.wait()
            batch = list(_pending)
            _pending.clear()
            lost, _dropped = _dropped, 0
            _inflight = len(batch)
        unwritten = len(batch) + lost
        try:
            unwritten = _write_batch(batch, lost)
        finally:
            with _STATE:
                _dropped += unwritten
                _inflight = 0
                _STATE.notify_all()


def _enqueue(path: Path, line: str) -> None:
    global _dropped, _worker, _exit_hook
    with _STATE:
        if len(_pending) >= _MAX_PENDING:
            _dropped += 1
            return
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, name=f"{PLUGIN_ID}-writer", daemon=True)
            _worker.start()
        if not _exit_hook:
            atexit.register(flush, _EXIT_FLUSH_SECONDS)
            _exit_hook = True
        _pending.append((path, line))
        _STATE.notify_all()


def flush(timeout: float = 5.0) -> bool:
    """Wait until every accepted row is on disk or counted as dropped; False if *timeout* hits first."""
    deadline = time.monotonic() + timeout
    with _STATE:
        while _pending or _inflight:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _STATE.wait(remaining)
    return True


def stats() -> dict[str, int]:
    with _STATE:
        return {"pending": len(_pending), "inflight": _inflight, "dropped": _dropped}


def observe(event: str, **payload: Any) -> None:
    global _dropped
    try:
        # The spool is resolved here, in the caller's profile scope; the writer has none.
        _enqueue(_event_path(), _encode(_row(event, payload)))
    except Exception:
        # Observer failures must never affect Hermes execution.
        with _STATE:
            _dropped += 1
    return None


def _callback(event: str):
    def callback(**payload: Any) -> None:
        return observe(event, **payload)
    callback.__name__ = f"observe_{event}"
    return callback


_CALLBACKS = {event: _callback(event) for event in _HOOKS}


def register(ctx) -> None:
    for event in _HOOKS:
        ctx.register_hook(event, _CALLBACKS[event])
