#!/usr/bin/env bash
# Restart rundeckd when its JVM runs files that a package upgrade replaced.
#
# A JDK replaced in place under a running JVM makes every job fail with "Spawn helper
# ran into unexpected internal error ... Restart JVM". Replacement leaves the old files
# mapped as "(deleted)" under /usr/lib/jvm, which is what this looks for, so it needs no
# list of packages and also catches `apt-get install --reinstall`.
#
# Run by rundeckd-java-restart.timer. The restart waits for a quiet runner: it asks the
# Rundeck API for running executions (what `rd executions list` shows) and does nothing
# while any exists, or while the answer is missing or malformed; the next tick retries.
# To close the gap between "none running" and the restart, it first puts Rundeck in
# passive execution mode (no new job starts), checks again, restarts, and returns it to
# active. While an operator holds Rundeck passive it waits, because a restart would
# reset that in-memory hold. If returning to active fails,
# /var/lib/rundeckd-java-restart.reenable stays and every later tick re-enables
# execution before doing anything else.
#
# The token is given to curl on stdin, never in its arguments.
# Test seams (gate/test-rundeckd-java-restart.py): RJR_MAPS, RJR_CRED, RJR_STATE.
set -uo pipefail

CRED=${RJR_CRED:-/root/.rundeck-bootstrap}
STATE=${RJR_STATE:-/var/lib/rundeckd-java-restart.reenable}

url=$(sed -n 's/^RUNDECK_URL=//p' "$CRED" 2>/dev/null | tail -1)
token=$(sed -n 's/^RUNDECK_API_TOKEN=//p' "$CRED" 2>/dev/null | tail -1)

api() { # api METHOD PATH -> body on stdout
  printf 'header = "X-Rundeck-Auth-Token: %s"\n' "$token" |
    curl -sf -m 20 -K - -X "$1" -H 'Accept: application/json' "$url/api/58/$2"
}
mode() { api GET system/executions/status | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["executionMode"])'; }
running() { api GET 'project/*/executions/running' | python3 -c '
import json,sys
e = json.load(sys.stdin)["executions"]
assert isinstance(e, list)
print(len(e))'; }
enable() { api POST system/executions/enable >/dev/null; }

# A previous run that could not give execution back finishes that first.
if [ -e "$STATE" ] && [ -n "$url" ] && [ -n "$token" ]; then
  if [ "$(mode 2>/dev/null)" = active ] || enable 2>/dev/null; then rm -f "$STATE"; fi
fi

pid=$(systemctl show -p MainPID --value rundeckd 2>/dev/null || echo 0)
[ "${pid:-0}" -gt 0 ] 2>/dev/null || exit 0
maps=${RJR_MAPS:-/proc/$pid/maps}
grep -qE '/usr/lib/jvm/.*\(deleted\)' "$maps" 2>/dev/null || exit 0

if [ -z "$url" ] || [ -z "$token" ]; then
  echo "rundeckd runs replaced Java files but $CRED has no API access; not restarting" >&2
  exit 0
fi
count=$(running) || { echo "no valid running-executions answer; will retry" >&2; exit 0; }
if [ "$count" != 0 ]; then
  echo "rundeckd needs a restart after a Java upgrade; waiting for $count running execution(s)"
  exit 0
fi

prior=$(mode) || { echo "cannot read the execution mode; will retry" >&2; exit 0; }
# Passive is an operator's hold, kept only in memory: a restart would start Rundeck in its
# configured mode and could open admission during the hold. Wait for the operator.
if [ "$prior" != active ]; then
  echo "rundeckd needs a restart after a Java upgrade; execution mode is $prior, waiting"
  exit 0
fi
touch "$STATE"
api POST system/executions/disable >/dev/null || { enable 2>/dev/null && rm -f "$STATE"; exit 0; }
count=$(running) || count=unknown
if [ "$count" != 0 ]; then
  echo "an execution started before the drain; not restarting"
  enable 2>/dev/null && rm -f "$STATE"
  exit 0
fi
echo "restarting rundeckd after a Java upgrade"
systemctl restart rundeckd
for _ in $(seq 60); do
  enable 2>/dev/null && { rm -f "$STATE"; break; }
  sleep 5
done
