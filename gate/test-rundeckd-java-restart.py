#!/usr/bin/env python3
"""The runner's rundeckd restart after a Java upgrade (ansible/files/runner/).

Runs the real hook against stub `systemctl` and `curl` (a small Rundeck API with an
execution mode): no restart while the JVM maps no replaced file; a restart, bracketed by
disabling and re-enabling execution, when it does and nothing runs; none while an
execution runs, or when the answer is missing, an error envelope or not valid; execution
mode is given back when an execution appears during the drain; the token never appears in
curl's arguments; a failed re-enable is finished by the next run. Also checks that
bootstrap and Guest Maintenance install the files, bootstrap from a root-owned clone.
"""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "ansible/files/runner"
HOOK = FILES / "rundeckd-java-restart.sh"

CURL = r'''#!/bin/bash
# Stub Rundeck API. $RJR_T holds: mode, running (JSON), second (JSON after disable), calls, bad_enable
case "$*" in *SECRET*) echo "token in argv" >> "$RJR_T/calls"; exit 22;; esac
grep -q 'X-Rundeck-Auth-Token: SECRET' || { echo "no token on stdin" >> "$RJR_T/calls"; exit 22; }
method=; url=
while [ $# -gt 0 ]; do case "$1" in -X) method=$2; shift;; http*) url=$1;; esac; shift; done
p=${url#http://x/api/58/}
echo "$method $p" >> "$RJR_T/calls"
case "$method $p" in
  "GET system/executions/status") echo "{\"executionMode\":\"$(cat "$RJR_T/mode")\"}";;
  "POST system/executions/disable") echo passive > "$RJR_T/mode"; [ -f "$RJR_T/second" ] && mv "$RJR_T/second" "$RJR_T/running"; echo '{}';;
  "POST system/executions/enable") [ -f "$RJR_T/bad_enable" ] && exit 22; echo active > "$RJR_T/mode"; echo '{}';;
  "GET project/*/executions/running") [ -f "$RJR_T/running" ] || exit 22; cat "$RJR_T/running";;
  *) exit 22;;
esac
'''


def run(maps, running, second=None, bad_enable=False, state_left=False, mode="active"):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "bin").mkdir()
        (d / "bin/systemctl").write_text(
            '#!/bin/sh\n[ "$1" = show ] && { echo 4242; exit 0; }\n'
            f'echo "systemctl $@" >> {d}/calls\n')
        (d / "bin/curl").write_text(CURL)
        (d / "bin/sleep").write_text("#!/bin/sh\n")
        for f in ("systemctl", "curl", "sleep"):
            (d / "bin" / f).chmod(0o755)
        (d / "maps").write_text(maps)
        (d / "mode").write_text(mode + "\n")
        (d / "cred").write_text("RUNDECK_URL=http://x\nRUNDECK_API_TOKEN=SECRET\n")
        if running is not None:
            (d / "running").write_text(running)
        if second is not None:
            (d / "second").write_text(second)
        if bad_enable:
            (d / "bad_enable").write_text("")
        if state_left:
            (d / "state").write_text("")
        env = {**os.environ, "PATH": f"{d}/bin:{os.environ['PATH']}", "RJR_T": str(d),
               "RJR_MAPS": str(d / "maps"), "RJR_CRED": str(d / "cred"),
               "RJR_STATE": str(d / "state")}
        subprocess.run(["bash", str(HOOK)], env=env, check=True, capture_output=True)
        calls = (d / "calls").read_text().splitlines() if (d / "calls").exists() else []
        return [c for c in calls if not c.startswith("GET")], (d / "state").exists(), \
            (d / "mode").read_text().strip()


CLEAN = "7f00 r-xp /usr/lib/jvm/java-21/lib/libjvm.so\n"
STALE = "7f00 r-xp /usr/lib/jvm/java-21/lib/libjvm.so (deleted)\n"
IDLE = '{"executions":[]}'
BUSY = '{"executions":[{"id":1}]}'
RESTART = ["POST system/executions/disable", "systemctl restart rundeckd",
           "POST system/executions/enable"]

assert run(CLEAN, IDLE)[0] == [], "restarted without a Java upgrade"
calls, state, mode = run(STALE, IDLE)
assert calls == RESTART and not state and mode == "active", (calls, state, mode)
assert run(STALE, IDLE, mode="passive")[0] == ["systemctl restart rundeckd"], \
    "changed an execution mode the operator had set"
assert run(STALE, BUSY)[0] == [], "restarted while an execution runs"
for bad in (None, "{}", '{"error":true,"message":"x"}', '{"executions":null}'):
    assert run(STALE, bad)[0] == [], f"restarted on an answer without a list: {bad}"
calls, state, mode = run(STALE, IDLE, second=BUSY)
assert "systemctl restart rundeckd" not in calls and mode == "active" and not state, \
    "restarted although an execution started during the drain"
calls, state, mode = run(STALE, IDLE, bad_enable=True)
assert state, "lost track of execution mode that could not be re-enabled"
calls, state, mode = run(CLEAN, IDLE, state_left=True, mode="passive")
assert mode == "active" and not state, "did not finish an interrupted re-enable"

boot = (ROOT / "rundeck/bootstrap-rundeck.sh").read_text()
assert 'git clone -q --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$rjr_src"' in boot
assert "/usr/local/sbin/rundeckd-java-restart" in boot and "$REPO_DIR/ansible/files/runner" not in boot
gm = (ROOT / "ansible/playbooks/maintenance/guest-maintenance.yml").read_text()
assert "install-rundeckd-java-restart.yml" in gm
task = (ROOT / "ansible/tasks/maintenance/install-rundeckd-java-restart.yml").read_text()
assert all(f in task for f in ("rundeckd-java-restart.sh", ".service", ".timer"))
assert "OnUnitActiveSec=5min" in (FILES / "rundeckd-java-restart.timer").read_text()
print("ok")
