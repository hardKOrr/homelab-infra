#!/usr/bin/env python3
"""The runner's rundeckd restart after a Java upgrade (ansible/files/runner/).

Runs the real hook against stub `systemctl` and `curl`: no restart while the JVM maps no
replaced file; a restart when it does and no execution runs; no restart while an execution
runs or the API cannot answer. Also checks that bootstrap and Guest Maintenance install it.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "ansible/files/runner/rundeckd-java-restart.sh"


def run(maps, api):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "bin").mkdir()
        log = d / "calls"
        (d / "bin/systemctl").write_text(
            '#!/bin/sh\n[ "$1" = show ] && { echo 4242; exit 0; }\n'
            f'echo "$@" >> {log}\n')
        (d / "bin/curl").write_text(
            "#!/bin/sh\n" + ("exit 22\n" if api is None else f"echo '{api}'\n"))
        for f in ("systemctl", "curl"):
            (d / "bin" / f).chmod(0o755)
        (d / "maps").write_text(maps)
        (d / "cred").write_text("RUNDECK_URL=http://x\nRUNDECK_API_TOKEN=t\n")
        env = {**os.environ, "PATH": f"{d}/bin:{os.environ['PATH']}",
               "RJR_MAPS": str(d / "maps"), "RJR_CRED": str(d / "cred")}
        subprocess.run(["bash", str(HOOK)], env=env, check=True, capture_output=True)
        return log.read_text().strip() if log.exists() else ""


CLEAN = "7f00 r-xp /usr/lib/jvm/java-21/lib/libjvm.so\n"
STALE = "7f00 r-xp /usr/lib/jvm/java-21/lib/libjvm.so (deleted)\n"
IDLE = '{"executions":[]}'
BUSY = '{"executions":[{"id":1}]}'

assert run(CLEAN, IDLE) == "", "restarted without a Java upgrade"
assert run(STALE, IDLE) == "restart rundeckd", "no restart after a Java upgrade"
assert run(STALE, BUSY) == "", "restarted while an execution runs"
assert run(STALE, None) == "", "restarted without knowing whether a job runs"

boot = (ROOT / "rundeck/bootstrap-rundeck.sh").read_text()
assert "runner-java-restart.yml" in boot
gm = (ROOT / "ansible/playbooks/maintenance/guest-maintenance.yml").read_text()
assert "install-rundeckd-java-restart.yml" in gm
task = (ROOT / "ansible/tasks/maintenance/install-rundeckd-java-restart.yml").read_text()
assert "rundeckd-java-restart.sh" in task and "OnUnitActiveSec=5min" in task
print("ok")
