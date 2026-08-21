#!/usr/bin/env bash
# Focused tests for ansible/scripts/maintenance-schedule.py -- the schedule resolver.
#
# Slice 205's whole premise is that ONE primitive expresses "reboot whenever it is
# needed", "reboot only in this window", and "never reboot without me". The cases that
# matter are therefore the ones where those meet: an app that declares a window on a
# guest whose other app declares `never`, and two windows that do not overlap. Getting
# either wrong reboots a service outside the window its owner was promised, which is
# the failure the primitive exists to prevent.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
sched="$repo/ansible/scripts/maintenance-schedule.py"

fail() { echo "maintenance-schedule test failed: $*" >&2; exit 1; }

# Expect success, and an exact value for one key of the answer.
expect() {
  local key="$1" want="$2" request="$3" got
  got="$(printf '%s' "$request" | python3 "$sched")" \
    || fail "request was refused but should have succeeded: $request"
  got="$(printf '%s' "$got" | python3 -c \
    "import json,sys; v=json.load(sys.stdin)['$key']; print('null' if v is None else v)")"
  [ "$got" = "$want" ] || fail "expected $key=$want, got $got, for: $request"
}

# Expect refusal, and a reason mentioning the given text. The message is the feature:
# a schedule the operator mistyped must say what it did not understand.
refuse() {
  local because="$1" request="$2" err rc
  set +e
  err="$(printf '%s' "$request" | python3 "$sched" 2>&1 >/dev/null)"
  rc=$?
  set -e
  [ "$rc" -ne 0 ] || fail "request should have been refused: $request"
  grep -qi -- "$because" <<<"$err" \
    || fail "refusal did not mention '$because' (said: $err)"
}

# One chain, global layer only. 2026-08-23 is a Sunday.
sunday_window='{"days":["Sun"],"start":"03:00","duration":120}'
g() { printf '{"name":"global","schedule":%s}' "$1"; }
a() { printf '{"name":"app %s","schedule":%s}' "$1" "$2"; }

# -- The three modes ----------------------------------------------------------------
expect mode always     "{\"chains\":[[$(g '"always"')]]}"
expect mode never      "{\"chains\":[[$(g '"never"')]]}"
expect mode window     "{\"chains\":[[$(g "$sunday_window")]]}"

# `never` is notify-only, and says so to the container updater as well.
expect monitor_only True "{\"chains\":[[$(g '"never"')]]}"
expect cron         null "{\"chains\":[[$(g '"never"')]]}"
expect monitor_only False "{\"chains\":[[$(g "$sunday_window")]]}"

# -- Due now ------------------------------------------------------------------------
# Inside the Sunday window, at its opening minute, and one minute before it opens.
expect due True  "{\"chains\":[[$(g "$sunday_window")]],\"now\":\"2026-08-23T04:00\"}"
expect due True  "{\"chains\":[[$(g "$sunday_window")]],\"now\":\"2026-08-23T03:00\"}"
expect due False "{\"chains\":[[$(g "$sunday_window")]],\"now\":\"2026-08-23T02:59\"}"
# The window closes at start+duration, exclusive -- 05:00 is already outside it.
expect due False "{\"chains\":[[$(g "$sunday_window")]],\"now\":\"2026-08-23T05:00\"}"
# Right day of the week, wrong week: the schedule repeats weekly.
expect due True  "{\"chains\":[[$(g "$sunday_window")]],\"now\":\"2026-08-30T03:30\"}"
# `always` is due at any moment; `never` at none.
expect due True  "{\"chains\":[[$(g '"always"')]],\"now\":\"2026-08-19T13:07\"}"
expect due False "{\"chains\":[[$(g '"never"')]],\"now\":\"2026-08-23T03:30\"}"

# -- Midnight wrap ------------------------------------------------------------------
# Saturday 23:00 for three hours runs into Sunday, and is ONE window, not two.
wrap='{"days":["Sat"],"start":"23:00","duration":180}'
expect due  True "{\"chains\":[[$(g "$wrap")]],\"now\":\"2026-08-23T01:30\"}"
expect due  True "{\"chains\":[[$(g "$wrap")]],\"now\":\"2026-08-22T23:30\"}"
expect due False "{\"chains\":[[$(g "$wrap")]],\"now\":\"2026-08-23T02:30\"}"
expect text "Sat 23:00 +180m" "{\"chains\":[[$(g "$wrap")]]}"

# A window that wraps the WEEK -- Sunday 23:00 into Monday -- is still one window.
weekwrap='{"days":["Sun"],"start":"23:00","duration":120}'
expect due  True "{\"chains\":[[$(g "$weekwrap")]],\"now\":\"2026-08-24T00:30\"}"
expect text "Sun 23:00 +120m" "{\"chains\":[[$(g "$weekwrap")]]}"

# -- The override chain -------------------------------------------------------------
# The narrowest layer that declares anything wins, and it wins WHOLE.
chain="[$(g '"always"'),{\"name\":\"estate\",\"schedule\":null},{\"name\":\"stack media\",\"schedule\":$sunday_window}]"
expect mode   window       "{\"chains\":[$chain]}"
# An app layer overrides the stack above it.
chain2="[$(g '"always"'),{\"name\":\"stack media\",\"schedule\":$sunday_window},$(a plex '"never"')]"
expect mode never "{\"chains\":[$chain2]}"
# An empty string is "inherit", not "a schedule I could not parse".
chain3="[$(g "$sunday_window"),{\"name\":\"app\",\"schedule\":\"\"}]"
expect mode window "{\"chains\":[$chain3]}"

# -- The shared-host rule -----------------------------------------------------------
# Two apps on one guest: the guest may only reboot inside BOTH windows.
early='{"days":["Sun"],"start":"01:00","duration":180}'
late='{"days":["Sun"],"start":"03:00","duration":180}'
both="{\"chains\":[[$(a sonarr "$early")],[$(a radarr "$late")]],\"now\":\"2026-08-23T03:30\"}"
expect mode window "$both"
expect due  True   "$both"
expect text "Sun 03:00 +60m" "{\"chains\":[[$(a sonarr "$early")],[$(a radarr "$late")]]}"
# 02:00 is inside sonarr's window but outside radarr's, so the guest stays up.
expect due False "{\"chains\":[[$(a sonarr "$early")],[$(a radarr "$late")]],\"now\":\"2026-08-23T02:00\"}"

# One `never` on a shared guest holds the whole guest, and names who did it.
held="{\"chains\":[[$(a sonarr "$early")],[$(a plex '"never"')]]}"
expect mode never "$held"
expect due  False "{\"chains\":[[$(a sonarr "$early")],[$(a plex '"never"')]],\"now\":\"2026-08-23T01:30\"}"
printf '%s' "$held" | python3 "$sched" | grep -q "app plex" \
  || fail "a guest held by one app's \`never\` must name that app in the conflict"

# Windows that never overlap are a conflict, not a coin toss.
disjoint="{\"chains\":[[$(a sonarr '{"days":["Sun"],"start":"01:00","duration":60}')],[$(a radarr '{"days":["Sun"],"start":"05:00","duration":60}')]]}"
expect mode never "$disjoint"
printf '%s' "$disjoint" | python3 "$sched" | grep -q "never overlap" \
  || fail "disjoint windows must be reported as a conflict"

# `always` constrains nothing, so it never narrows a peer's window.
mixed="{\"chains\":[[$(a sonarr '"always"')],[$(a radarr "$late")]]}"
expect mode window "$mixed"
expect text "Sun 03:00 +180m" "$mixed"

# -- The cron the container updater receives ----------------------------------------
expect cron "0 0 3 * * 0" "{\"chains\":[[$(g "$sunday_window")]]}"          # Sunday = 0
expect cron "0 0 * * * *" "{\"chains\":[[$(g '"always"')]]}"
expect cron "0 30 2 * * *" "{\"chains\":[[$(g '{"days":"any","start":"02:30","duration":60}')]]}"
expect cron "0 0 4 * * 1,3" "{\"chains\":[[$(g '{"days":["Mon","Wed"],"start":"04:00","duration":60}')]]}"

# -- The OnCalendar the guest's own timer receives ----------------------------------
# This is how a schedule is ENFORCED: the guest holds a systemd timer for its own window
# and reboots itself when it opens. Nothing polls, so these strings are the mechanism and
# not a display detail.
oncal() {
  printf '%s' "$1" | python3 "$sched"     | python3 -c "import json,sys; print('|'.join(json.load(sys.stdin)['oncalendar']))"
}
expect_oncal() {
  local want="$1" request="$2" got
  got="$(oncal "$request")"
  [ "$got" = "$want" ] || fail "expected oncalendar '$want', got '$got', for: $request"
}

expect_oncal "*-*-* 04:00:00"     "{\"chains\":[[$(g '{"days":"any","start":"04:00","duration":120}')]]}"
expect_oncal "Sun *-*-* 03:00:00" "{\"chains\":[[$(g "$sunday_window")]]}"
expect_oncal "Mon,Wed *-*-* 04:00:00" "{\"chains\":[[$(g '{"days":["Mon","Wed"],"start":"04:00","duration":60}')]]}"
# `always` still has to name a moment once it becomes a timer; hourly is that moment.
expect_oncal "*-*-* *:00:00"      "{\"chains\":[[$(g '"always"')]]}"
# `never` yields no lines at all, and the caller REMOVES the timer — notify only.
expect_oncal ""                   "{\"chains\":[[$(g '"never"')]]}"
# A window that wraps midnight opens on the day it opens, not on the day it ends.
expect_oncal "Sat *-*-* 23:00:00" "{\"chains\":[[$(g "$wrap")]]}"
# An intersected shared-host window is enforced as the intersection, not as either input.
expect_oncal "Sun *-*-* 03:00:00" "{\"chains\":[[$(a sonarr "$early")],[$(a radarr "$late")]]}"

# -- Refusals -----------------------------------------------------------------------
refuse "not 'always', 'never'"  "{\"chains\":[[$(g '"sometimes"')]]}"
refuse "does not know the day"  "{\"chains\":[[$(g '{"days":["Funday"],"start":"03:00","duration":60}')]]}"
refuse "HH:MM"                  "{\"chains\":[[$(g '{"days":["Sun"],"start":"3am","duration":60}')]]}"
refuse "not a time of day"      "{\"chains\":[[$(g '{"days":["Sun"],"start":"25:00","duration":60}')]]}"
refuse "duration"               "{\"chains\":[[$(g '{"days":["Sun"],"start":"03:00"}')]]}"
refuse "at least 1 minute"      "{\"chains\":[[$(g '{"days":["Sun"],"start":"03:00","duration":0}')]]}"
refuse "declares no schedule"   '{"chains":[[{"name":"global","schedule":null}]]}'
refuse 'non-empty `chains`'     '{}'

echo "maintenance-schedule: all cases passed."
