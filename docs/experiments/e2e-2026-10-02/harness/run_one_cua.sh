#!/usr/bin/env bash
# run_one_cua.sh: ONE Hermes computer-use run (bend plugin enabled, stack_mode shadow) inside a private X11 session.
# Adapted from the accepted stack2-addr run_one.sh: same fixtures, prompts, allow-list and oracle. Hermes runs through
# the bend-e2e launcher (private overlay home, live Hermes home / real home / host /run/user / host X11 masked).
# Start as: hostless env CUA_SESSION_ATSPI=1 CUA_SESSION_EXTRA_ENV="CUA_SESSION_ATSPI=1 CUA_HOSTLESS=1 E2E_ENV=<out>/run.env" \
#              cua-x11-session.sh run_one_cua.sh
# <out>/run.env (local, never committed) supplies: OUT ID TASK PROMPT TOKEN E2E_HOME CONFIG HERMES_TIMEOUT MAX_TURNS FIXTURE_DIR
#   PM_INSTALLS_MASK (the profile PM install state, hidden so Hermes runs on the dedicated venv, see README)
set -uo pipefail
case "${XDG_RUNTIME_DIR:-}" in */x11-session.*/xdg-runtime) ;; *) echo "refusing: not inside the private X11 session" >&2; exit 97;; esac
[ -n "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] \
  || { echo "refusing: host display variables present" >&2; exit 97; }
# shellcheck disable=SC1090
. "$E2E_ENV"
mkdir -p "$OUT/fixture" "$OUT/meta" "$OUT/tmp"
log() { printf '%s %s\n' "$(date -u +%FT%T.%3NZ)" "$*" >> "$OUT/meta/timeline.log"; }
env | grep -E '^(DISPLAY|XDG_SESSION_TYPE|XDG_RUNTIME_DIR|WAYLAND_DISPLAY|HYPRLAND_INSTANCE_SIGNATURE|CUA_HOSTLESS)=' \
  | sed "s|=.*/x11-session\.|=<session>/x11-session.|" | sort > "$OUT/meta/session.env"
log "session start task=$TASK id=$ID"

# --- fixture (app-owned state; the oracle never asks Hermes, the observer or the Driver) ---
PIDS=()
if [ "$TASK" = cua_gtk3 ]; then
  CUA_GTK3_TASK_STATE="$OUT/fixture/gtk3-state.json" /usr/bin/python3 "$FIXTURE_DIR/gtk3_main.py" > "$OUT/fixture/app.log" 2>&1 &
  PIDS+=($!)
  for _ in $(seq 50); do [ -s "$OUT/fixture/gtk3-state.json" ] && break; sleep 0.2; done
  cp "$OUT/fixture/gtk3-state.json" "$OUT/fixture/state.before.json"
else
  /usr/bin/python3 "$FIXTURE_DIR/fixture_serve.py" "$FIXTURE_DIR/jev_use_fixture_server.py" "$OUT/fixture/port" \
    > "$OUT/fixture/server.log" 2>&1 &
  PIDS+=($!)
  for _ in $(seq 50); do [ -s "$OUT/fixture/port" ] && break; sleep 0.1; done
  PORT="$(cat "$OUT/fixture/port")"
  curl -s --noproxy '*' "http://127.0.0.1:$PORT/state" > "$OUT/fixture/state.before.json"
  ACCESSIBILITY_ENABLED=1 /opt/google/chrome/chrome --user-data-dir="$OUT/tmp/chrome-profile" --no-first-run \
    --no-default-browser-check --disable-sync --password-store=basic --disable-background-networking \
    --disable-component-update --no-pings --proxy-server=http://127.0.0.1:9 --force-renderer-accessibility \
    --window-size=1100,800 --app="http://127.0.0.1:$PORT/" > "$OUT/fixture/chrome.log" 2>&1 &
  PIDS+=($!)
  sleep 4
  dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus org.freedesktop.DBus.Properties.Set \
    string:org.a11y.Status string:IsEnabled variant:boolean:true > /dev/null 2>&1
  sleep 4
fi
log "fixture ready"

# --- Hermes through the bend-e2e launcher (CUA_HOSTLESS=1: already under hostless inside the private session) ---
log "hermes start"
BEND_CONFIG="$CONFIG" BEND_GUI=1 BEND_RUN_TIMEOUT="$HERMES_TIMEOUT" BEND_MASK_DIRS="$OUT/fixture $PM_INSTALLS_MASK" \
BEND_EXTRA_ENV="DISPLAY=$DISPLAY DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS XDG_SESSION_TYPE=x11" \
  "$E2E_HOME/bin/run-hermes-e2e.sh" "$ID" -m hermes_cli.main chat -Q -t computer_use --max-turns "$MAX_TURNS" -q "$PROMPT" \
  > "$OUT/meta/launcher.out" 2>&1
rc=$?
echo "$rc" > "$OUT/meta/launcher_rc"
log "hermes end rc=$rc"

# --- independent oracle: fixture-owned state, read by this harness after Hermes exited ---
if [ "$TASK" = cua_gtk3 ]; then
  cp "$OUT/fixture/gtk3-state.json" "$OUT/fixture/state.after.json"
  OT=gtk3
else
  curl -s --noproxy '*' "http://127.0.0.1:$PORT/state" > "$OUT/fixture/state.after.json"
  OT=browser
fi
/usr/bin/python3 "$E2E_HOME/bin/oracle_cua.py" "$OT" "$TOKEN" "$OUT/fixture/state.before.json" "$OUT/fixture/state.after.json" \
  > "$OUT/oracle.json"
log "oracle $(cat "$OUT/oracle.json")"
for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
wait 2>/dev/null
log "session end"
exit 0
