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
# while any exists, or while the answer is unknown. The next tick tries again.
#
# Test seams (gate/test-rundeckd-java-restart.py): RJR_MAPS, RJR_CRED.
set -uo pipefail

CRED=${RJR_CRED:-/root/.rundeck-bootstrap}
pid=$(systemctl show -p MainPID --value rundeckd 2>/dev/null || echo 0)
[ "${pid:-0}" -gt 0 ] 2>/dev/null || exit 0
maps=${RJR_MAPS:-/proc/$pid/maps}
grep -qE '/usr/lib/jvm/.*\(deleted\)' "$maps" 2>/dev/null || exit 0

url=$(sed -n 's/^RUNDECK_URL=//p' "$CRED" 2>/dev/null | tail -1)
token=$(sed -n 's/^RUNDECK_API_TOKEN=//p' "$CRED" 2>/dev/null | tail -1)
if [ -z "$url" ] || [ -z "$token" ]; then
  echo "rundeckd runs replaced Java files but $CRED has no API access; not restarting" >&2
  exit 0
fi
body=$(curl -sf -m 20 -H "X-Rundeck-Auth-Token: $token" -H 'Accept: application/json' \
  "$url/api/58/executions/running?project=*") || {
  echo "cannot list running executions; will retry" >&2
  exit 0
}
count=$(printf '%s' "$body" | python3 -c \
  'import json,sys; print(len(json.load(sys.stdin).get("executions", [])))') || exit 0
if [ "$count" != 0 ]; then
  echo "rundeckd needs a restart after a Java upgrade; waiting for $count running execution(s)"
  exit 0
fi
echo "restarting rundeckd after a Java upgrade"
systemctl restart rundeckd
