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
# passive execution mode (no new job starts), checks again, including the scheduler's
# running-job count (a dispatched job may have no execution record yet), restarts, and returns it to
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
# Jobs Quartz already dispatched can still be starting, with no execution record yet.
scheduled() { api GET system/info | python3 -c '
import json,sys
n = json.load(sys.stdin)["system"]["stats"]["scheduler"]["running"]
assert isinstance(n, int) and not isinstance(n, bool)
print(n)'; }
enable() { api POST system/executions/enable >/dev/null; }

# restored: execution is active again, confirmed by the server's own status.
restored() { enable 2>/dev/null && [ "$(mode 2>/dev/null)" = active ]; }
mainpid() { systemctl show -p MainPID --value rundeckd 2>/dev/null || echo 0; }

# A previous run that could not give execution back, or a failed restart, finishes first.
if [ -e "$STATE" ] && [ -n "$url" ] && [ -n "$token" ]; then
  [ "$(mainpid)" -gt 0 ] 2>/dev/null || systemctl restart rundeckd 2>/dev/null
  if restored; then rm -f "$STATE"; fi
fi

pid=$(mainpid)
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
case "$prior" in
  active) ;;
  passive) echo "rundeckd needs a restart after a Java upgrade; execution is passive, waiting"
    exit 0 ;;
  *) echo "unrecognised execution mode '$prior'; will retry" >&2; exit 0 ;;
esac
# The marker comes first: without it an interrupted run could leave execution disabled.
touch "$STATE" 2>/dev/null || { echo "cannot write $STATE; not restarting" >&2; exit 0; }
api POST system/executions/disable >/dev/null 2>&1
if [ "$(mode 2>/dev/null)" != passive ]; then
  echo "execution did not become passive; not restarting" >&2
  restored && rm -f "$STATE"
  exit 0
fi
count=$(running) || count=unknown
sched=$(scheduled) || sched=unknown
if [ "$count" != 0 ] || [ "$sched" != 0 ]; then
  echo "an execution started before the drain; not restarting"
  restored && rm -f "$STATE"
  exit 0
fi
echo "restarting rundeckd after a Java upgrade"
if ! systemctl restart rundeckd; then
  echo "systemctl restart rundeckd failed; later runs retry it" >&2
  exit 1
fi
for _ in $(seq 60); do
  restored && { rm -f "$STATE"; exit 0; }
  sleep 5
done
echo "rundeckd restarted but execution could not be confirmed active; later runs retry" >&2
exit 1
