#!/usr/bin/env bash
# run-hermes.sh <run-id> <python args...>   (bend-stack; derived from the stack SAMPLES/stack-integration launchers)
# AODL lane derivative: identical to bend-hermes-home/run-hermes.sh except BEND_NET_NONE=1 adds bwrap --unshare-net (no network namespace route; loopback only); also records meta/net.inside (netns id + interfaces).
# Runs the bend-integration Hermes worktree from its dedicated venv in a fresh private run dir:
#   - hostless (desktop env stripped, private XDG_RUNTIME_DIR, Landlock scope). When already inside a
#     private session (CUA_HOSTLESS=1, e.g. cua-x11-session.sh under hostless) it is not re-applied.
#   - nested bwrap masks the live Hermes home (${LIVE_HERMES_HOME}, target of ~/.hermes), the real
#     $HOME, the host /run/user/<uid> and /tmp/.X11-unix with empty tmpfs (BEND_GUI=1 re-binds only the
#     private session's own X socket).
#   - HOME/HERMES_HOME = the template home seen through a per-run overlay (writes kept in runs/<id>/home-upper,
#     absolute paths of the PM-managed profile stay valid); the PM runtime store is mounted read-only.
#   - env -i allow-list: no inherited HERMES_HOME, API keys or tokens; TMPDIR/XDG_*
#     inside the run dir; HTTP(S) egress proxied to a dead port except loopback (Ollama 127.0.0.1:11500,
#     a private z0int instance); Lean 4.34.0 on PATH for Bend's kernel build.
# Knobs: BEND_FIXTURE_DIR (copied into cwd), BEND_RUN_TIMEOUT (default 900 s), BEND_EXTRA_ENV (K=V pairs),
#   BEND_GUI=1 (inside a private X session), BEND_EXEC=raw (exec "$@" instead of venv python),
#   BEND_TEMPLATE_EDIT=1 (setup only: write the template home in place; otherwise an overlay per run),
#   BEND_VERIFIER=patched (bind config-patched.yaml: settings.executable = the kvnloo/bend@17db447a build),
#   BEND_CUA_DRIVER (driver path override), BEND_PYTHON (interpreter; default the dedicated uv venv).
# Example: run-hermes.sh doctor-01 -m hermes_cli.main bend doctor
set -euo pipefail
LANE=${HERMES_LANE}
WT=${HERMES_WT}
VENV=${HERMES_VENV}
LEAN_BIN=${LEAN}/bin
RUNTIME="${BEND_RUNTIME_DIR:-${HERMES_PM_RUNTIME}}"
DRIVER="${BEND_CUA_DRIVER:-${CUA_DRIVER}}"
HOSTLESS=${CUA_LANES}/bin/hostless
REAL_HOME_MASK=${REAL_HOME}
LIVE_HERMES_HOME=${LIVE_HERMES_HOME}

run_id="$1"; shift
RUN="$LANE/runs/$run_id"
[ -e "$RUN" ] && { echo "run dir exists: $RUN" >&2; exit 2; }
mkdir -p "$RUN"
TEMPLATE="$LANE/template/home"
RHOME="$TEMPLATE"
if [ "${BEND_TEMPLATE_EDIT:-0}" = 1 ]; then
  # setup only: write the template in place; the PM runtime store stays writable
  HOMEBIND=(); RTBIND=()
else
  # every run sees the template at its own absolute path through an overlay; writes land in
  # $RUN/home-upper and the template never changes. The PM runtime store is read-only.
  mkdir -p "$RUN/home-upper" "$RUN/home-work"
  HOMEBIND=(--overlay-src "$TEMPLATE" --overlay "$RUN/home-upper" "$RUN/home-work" "$TEMPLATE")
  RTBIND=(--ro-bind "$RUNTIME" "$RUNTIME")
  if [ "${BEND_VERIFIER:-official}" = patched ]; then
    # per-run copy of the patched-verifier profile config, bound over the template config.yaml
    cp -a "$LANE/config-patched.yaml" "$RUN/config.yaml"
    HOMEBIND+=(--bind "$RUN/config.yaml" "$TEMPLATE/.hermes/config.yaml")
  fi
fi
mkdir -p "$RUN/tmp" "$RUN/cwd" "$RUN/meta" "$RUN/xdg"
chmod 700 "$RUN/xdg"
[ -d "${BEND_FIXTURE_DIR:-/nonexistent}" ] && cp -a "$BEND_FIXTURE_DIR/." "$RUN/cwd/"

snap() { for p in "$LIVE_HERMES_HOME" "$LIVE_HERMES_HOME/state.db" "$LIVE_HERMES_HOME/state.db-wal" \
                  "$LIVE_HERMES_HOME/config.yaml" "$LIVE_HERMES_HOME/.env" "$LIVE_HERMES_HOME/auth.json" \
                  "$LIVE_HERMES_HOME/logs"; do
           stat -c '%n|%y|%s' "$p" 2>/dev/null || echo "$p|absent"; done; }
snap > "$RUN/meta/live-home-stat.before"
git -C "$WT" rev-parse HEAD > "$RUN/meta/worktree-head"
git -C "$WT" status --porcelain > "$RUN/meta/worktree-status"
printf '%s\n' "$@" > "$RUN/meta/argv"
echo "$RHOME" > "$RUN/meta/home"
date -u +%FT%T.%3NZ > "$RUN/meta/started_at"

if [ "${CUA_HOSTLESS:-}" = 1 ]; then OUTER=(); else OUTER=("$HOSTLESS"); fi
MASKS=(--tmpfs "/run/user/$(id -u)" --tmpfs /tmp/.X11-unix)
if [ "${BEND_GUI:-0}" = 1 ]; then
  dnum="${DISPLAY#:}"; dnum="${dnum%%.*}"
  [ -S "/tmp/.X11-unix/X$dnum" ] || { echo "run-hermes: private display socket X$dnum missing" >&2; exit 96; }
  MASKS+=(--bind "/tmp/.X11-unix/X$dnum" "/tmp/.X11-unix/X$dnum")
fi
if [ "${BEND_EXEC:-python}" = raw ]; then EXECPY=0; else EXECPY=1; fi
PYBIN="${BEND_PYTHON:-$VENV/bin/python}"; echo "$PYBIN" > "$RUN/meta/python"
set +e
"${OUTER[@]}" bwrap --dev-bind / / ${BEND_NET_NONE:+--unshare-net} \
    --tmpfs "$LIVE_HERMES_HOME" --tmpfs "$REAL_HOME_MASK" "${MASKS[@]}" "${HOMEBIND[@]}" "${RTBIND[@]}" \
    --chdir "$RUN/cwd" --die-with-parent -- \
  env -i \
    PATH="$VENV/bin:$LEAN_BIN:/usr/local/bin:/usr/bin:/bin" \
    VIRTUAL_ENV="$VENV" \
    HOME="$RHOME" \
    HERMES_HOME="$RHOME/.hermes" \
    TMPDIR="$RUN/tmp" \
    XDG_RUNTIME_DIR="$RUN/xdg" \
    XDG_CONFIG_HOME="$RHOME/.config" XDG_CACHE_HOME="$RHOME/.cache" \
    XDG_DATA_HOME="$RHOME/.local/share" XDG_STATE_HOME="$RHOME/.local/state" \
    LANG=C.UTF-8 TZ=UTC TERM=dumb NO_COLOR=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX="$RUN/pycache" \
    CUA_HOSTLESS=1 BEND_NO_TELEMETRY=1 HF_HUB_OFFLINE=1 \
    HERMES_RUNTIME_DIR="$RUNTIME" UV_CACHE_DIR=${UV_CACHE} \
    HERMES_CUA_DRIVER_CMD="$DRIVER" \
    CUA_DRIVER_RS_TELEMETRY_ENABLED=0 \
    HERMES_API_TIMEOUT="${BEND_API_TIMEOUT:-600}" \
    HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
    http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 all_proxy=http://127.0.0.1:9 \
    NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
    ${BEND_EXTRA_ENV:-} \
  bash -c '
    meta="$1"; to="$2"; execpy="$3"; py="$4"; shift 4
    env | sort > "$meta/env.inside"
    { echo "live_hermes_home_entries=$(ls -A '"$LIVE_HERMES_HOME"' | wc -l)";
      echo "real_home_entries=$(ls -A '"$REAL_HOME_MASK"' | wc -l)";
      echo "run_user_entries=$(ls -A /run/user/$(id -u) 2>/dev/null | wc -l)";
      echo "x11_entries=$(ls -A /tmp/.X11-unix 2>/dev/null | tr "\n" " ")"; } > "$meta/mask.inside"
    { echo "netns=$(readlink /proc/self/ns/net)"; echo "ifaces=$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d " " | tr "\n" " ")"; } > "$meta/net.inside"
    if [ "$execpy" = 1 ]; then exec timeout --kill-after=15 "$to" "$py" "$@"; fi
    exec timeout --kill-after=15 "$to" "$@"
  ' _ "$RUN/meta" "${BEND_RUN_TIMEOUT:-900}" "$EXECPY" "$PYBIN" "$@" > "$RUN/meta/stdout" 2> "$RUN/meta/stderr"
rc=$?
set -e
date -u +%FT%T.%3NZ > "$RUN/meta/ended_at"
echo "$rc" > "$RUN/meta/exit_code"
snap > "$RUN/meta/live-home-stat.after"
[ -d "$RUN/home-upper" ] && (cd "$RUN/home-upper" && find . -mindepth 1 | LC_ALL=C sort > "$RUN/meta/home-upper.list")
echo "run=$RUN rc=$rc"
exit "$rc"
