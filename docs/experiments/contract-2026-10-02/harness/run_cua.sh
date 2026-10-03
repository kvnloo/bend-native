#!/usr/bin/env bash
# run_cua.sh: ONE Hermes computer-use run of the GTK3 fixture task inside a private X11 session (contract lane).
# Adapted from the accepted stack2-addr run_one.sh (gtk3 branch): same fixture, prompt, allow-list and oracle.
# Started by drive.py as:  hostless cua-x11-session.sh run_cua.sh   with CUA_SESSION_ATSPI=1 and
#   CUA_SESSION_EXTRA_ENV="CUA_SESSION_ATSPI=1 CUA_HOSTLESS=1 CONTRACT_ENV=<run>/run.env"
# <run>/run.env (local, never committed) supplies RUN FIXTURE_GTK HARNESS PROMPT MAX_TURNS TOOLSETS plus the
# launch.sh path variables.
set -uo pipefail
case "${XDG_RUNTIME_DIR:-}" in */x11-session.*/xdg-runtime) ;; *) echo "refusing: not inside the private X11 session" >&2; exit 97;; esac
[ -n "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] \
  || { echo "refusing: host display variables present" >&2; exit 97; }
# shellcheck disable=SC1090
set -a; . "$CONTRACT_ENV"; set +a
mkdir -p "$RUN/fixture"
env | grep -E '^(DISPLAY|XDG_SESSION_TYPE|XDG_RUNTIME_DIR|WAYLAND_DISPLAY|HYPRLAND_INSTANCE_SIGNATURE|CUA_HOSTLESS)=' \
  | sed "s|=.*/x11-session\.[^/]*/|=<session>/|" | sort > "$RUN/session.env"

CUA_GTK3_TASK_STATE="$RUN/fixture/gtk3-state.json" /usr/bin/python3 "$FIXTURE_GTK" > "$RUN/fixture/app.log" 2>&1 &
APP=$!
for _ in $(seq 50); do [ -s "$RUN/fixture/gtk3-state.json" ] && break; sleep 0.2; done
cp "$RUN/fixture/gtk3-state.json" "$RUN/fixture-state.before.json" 2>/dev/null

BEND_GUI=1 BEND_EXTRA_TMPFS="$RUN/fixture" \
BEND_EXTRA_ENV="DISPLAY=$DISPLAY DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS XDG_SESSION_TYPE=x11 XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR" \
  bash "$HARNESS/launch.sh" "$RUN" -m hermes_cli.main chat -Q -t "$TOOLSETS" --max-turns "$MAX_TURNS" -q "$PROMPT"
rc=$?

# independent oracle: fixture-owned state, read by this harness after Hermes exited
cp "$RUN/fixture/gtk3-state.json" "$RUN/fixture-state.after.json" 2>/dev/null
/usr/bin/python3 "$HARNESS/oracle.py" "$RUN/fixture-state.before.json" "$RUN/fixture-state.after.json" > "$RUN/oracle.json"
kill "$APP" 2>/dev/null; wait 2>/dev/null
exit "$rc"
