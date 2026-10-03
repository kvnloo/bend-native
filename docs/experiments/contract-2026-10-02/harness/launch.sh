#!/usr/bin/env bash
# launch.sh <run-dir> <python args...>   (contract lane; derived from the bend-stack setup launcher run-hermes.sh)
# Runs the integration Hermes worktree from its dedicated venv for ONE prepared run dir:
#   - hostless unless already inside it (CUA_HOSTLESS=1, e.g. under drive.py or inside a private X session);
#   - nested bwrap masks the live Hermes home, the real $HOME, the host /run/user/<uid> and /tmp/.X11-unix with
#     empty tmpfs (BEND_GUI=1 re-binds only the private session's own X socket); BEND_EXTRA_TMPFS adds masks
#     (the CUA fixture's state dir, so Hermes can never read the oracle's input);
#   - HOME/HERMES_HOME = the setup template home seen through a per-run overlay (writes kept in <run>/home-upper,
#     the template never changes); the run's config.yaml (written by drive.py: arm settings + capture-proxy
#     base_url) is bound over the template's config.yaml;
#   - the run's cwd/tmp/xdg dirs are bound at ONE stable path for every run, so paths inside provider
#     payloads are identical across arms (runs are sequential);
#   - env -i allow-list; HTTP(S) egress to a dead proxy except loopback.
# Every machine path comes from the environment (lane.env, local only, never committed):
#   TEMPLATE (path seen inside the sandbox) TEMPLATE_SRC (host copy overlaid there) WT VENV LEAN_BIN RUNTIME
#   DRIVER HOSTLESS REAL_HOME_MASK LIVE_HERMES_HOME STABLE UV_CACHE
set -euo pipefail
RUN="$1"; shift
for v in TEMPLATE TEMPLATE_SRC WT VENV LEAN_BIN RUNTIME DRIVER HOSTLESS REAL_HOME_MASK LIVE_HERMES_HOME STABLE UV_CACHE; do
  [ -n "${!v:-}" ] || { echo "launch: $v unset" >&2; exit 2; }
done
[ -f "$RUN/config.yaml" ] || { echo "launch: $RUN/config.yaml missing (prepare the run first)" >&2; exit 2; }
[ -e "$RUN/meta" ] && { echo "launch: $RUN already ran" >&2; exit 2; }
mkdir -p "$RUN/home-upper" "$RUN/home-work" "$RUN/tmp" "$RUN/cwd" "$RUN/meta" "$RUN/xdg" \
         "$STABLE/cwd" "$STABLE/tmp" "$STABLE/xdg"
chmod 700 "$RUN/xdg"
HOMEBIND=(--overlay-src "$TEMPLATE_SRC" --overlay "$RUN/home-upper" "$RUN/home-work" "$TEMPLATE"
          --bind "$RUN/config.yaml" "$TEMPLATE/.hermes/config.yaml" --ro-bind "$RUNTIME" "$RUNTIME")
STABLEBIND=(--bind "$RUN/cwd" "$STABLE/cwd" --bind "$RUN/tmp" "$STABLE/tmp" --bind "$RUN/xdg" "$STABLE/xdg")

snap() { for p in "$LIVE_HERMES_HOME" "$LIVE_HERMES_HOME/state.db" "$LIVE_HERMES_HOME/state.db-wal" \
                  "$LIVE_HERMES_HOME/config.yaml" "$LIVE_HERMES_HOME/.env" "$LIVE_HERMES_HOME/auth.json" \
                  "$LIVE_HERMES_HOME/logs"; do
           stat -c '%n|%y|%s' "$p" 2>/dev/null || echo "$p|absent"; done; }
snap > "$RUN/meta/live-home-stat.before"
git -C "$WT" rev-parse HEAD > "$RUN/meta/worktree-head"
git -C "$WT" status --porcelain > "$RUN/meta/worktree-status"
git -C "$TEMPLATE_SRC/.hermes/plugins/bend" rev-parse HEAD > "$RUN/meta/plugin-head"
git -C "$TEMPLATE_SRC/.hermes/plugins/bend" status --porcelain > "$RUN/meta/plugin-status"
sha256sum "$RUN/config.yaml" | cut -c1-64 > "$RUN/meta/config-sha256"
printf '%s\n' "$@" > "$RUN/meta/argv"
date -u +%FT%T.%3NZ > "$RUN/meta/started_at"

if [ "${CUA_HOSTLESS:-}" = 1 ]; then OUTER=(); else OUTER=("$HOSTLESS"); fi
MASKS=(--tmpfs "/run/user/$(id -u)" --tmpfs /tmp/.X11-unix)
for m in ${BEND_EXTRA_TMPFS:-}; do MASKS+=(--tmpfs "$m"); done
if [ "${BEND_GUI:-0}" = 1 ]; then
  dnum="${DISPLAY#:}"; dnum="${dnum%%.*}"
  [ -S "/tmp/.X11-unix/X$dnum" ] || { echo "launch: private display socket X$dnum missing" >&2; exit 96; }
  MASKS+=(--bind "/tmp/.X11-unix/X$dnum" "/tmp/.X11-unix/X$dnum")
fi
if [ "${BEND_EXEC:-python}" = raw ]; then EXECPY=0; else EXECPY=1; fi
PYBIN="$VENV/bin/python"
set +e
"${OUTER[@]}" bwrap --dev-bind / / \
    --tmpfs "$LIVE_HERMES_HOME" --tmpfs "$REAL_HOME_MASK" "${MASKS[@]}" "${HOMEBIND[@]}" "${STABLEBIND[@]}" \
    --chdir "$STABLE/cwd" --die-with-parent -- \
  env -i \
    PATH="$VENV/bin:$LEAN_BIN:/usr/local/bin:/usr/bin:/bin" \
    VIRTUAL_ENV="$VENV" \
    HOME="$TEMPLATE" \
    HERMES_HOME="$TEMPLATE/.hermes" \
    TMPDIR="$STABLE/tmp" \
    XDG_RUNTIME_DIR="$STABLE/xdg" \
    XDG_CONFIG_HOME="$TEMPLATE/.config" XDG_CACHE_HOME="$TEMPLATE/.cache" \
    XDG_DATA_HOME="$TEMPLATE/.local/share" XDG_STATE_HOME="$TEMPLATE/.local/state" \
    LANG=C.UTF-8 TZ=UTC TERM=dumb NO_COLOR=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX="$RUN/pycache" \
    CUA_HOSTLESS=1 BEND_NO_TELEMETRY=1 HF_HUB_OFFLINE=1 \
    HERMES_RUNTIME_DIR="$RUNTIME" UV_CACHE_DIR="$UV_CACHE" \
    HERMES_CUA_DRIVER_CMD="$DRIVER" \
    CUA_DRIVER_RS_TELEMETRY_ENABLED=0 \
    HERMES_API_TIMEOUT="${BEND_API_TIMEOUT:-600}" \
    HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
    http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 all_proxy=http://127.0.0.1:9 \
    NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
    ${BEND_EXTRA_ENV:-} \
  bash -c '
    meta="$1"; to="$2"; execpy="$3"; py="$4"; live="$5"; real="$6"; shift 6
    env | sort > "$meta/env.inside"
    { echo "live_hermes_home_entries=$(ls -A "$live" | wc -l)";
      echo "real_home_entries=$(ls -A "$real" | wc -l)";
      echo "run_user_entries=$(ls -A /run/user/$(id -u) 2>/dev/null | wc -l)";
      echo "x11_entries=$(ls -A /tmp/.X11-unix 2>/dev/null | tr "\n" " ")"; } > "$meta/mask.inside"
    for m in '"${BEND_EXTRA_TMPFS:-}"'; do echo "extra_mask_entries $(basename "$m")=$(ls -A "$m" | wc -l)"; done >> "$meta/mask.inside"
    if [ "$execpy" = 1 ]; then exec timeout --kill-after=15 "$to" "$py" "$@"; fi
    exec timeout --kill-after=15 "$to" "$@"
  ' _ "$RUN/meta" "${BEND_RUN_TIMEOUT:-900}" "$EXECPY" "$PYBIN" "$LIVE_HERMES_HOME" "$REAL_HOME_MASK" "$@" \
  > "$RUN/meta/stdout" 2> "$RUN/meta/stderr"
rc=$?
set -e
date -u +%FT%T.%3NZ > "$RUN/meta/ended_at"
echo "$rc" > "$RUN/meta/exit_code"
snap > "$RUN/meta/live-home-stat.after"
(cd "$RUN/home-upper" && find . -mindepth 1 | LC_ALL=C sort > "$RUN/meta/home-upper.list")
echo "run=$RUN rc=$rc"
exit "$rc"
