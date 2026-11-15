#!/usr/bin/env python3
"""tools/lab-drill --plan: the drill's sequence and canary choice, without a lab."""
import os
import subprocess
import sys

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def plan(*args):
    out = subprocess.run([sys.executable, os.path.join(repo, "tools", "lab-drill"), "--plan", *args],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if out.returncode != 0:
        failures.append(f"{args}: exit {out.returncode}: {out.stderr}")
    return out.stdout


def steps(text):
    return [line.split()[1].split("-", 1)[1] for line in text.splitlines() if line.startswith("STEP ")]


STATEFUL = ["deploy", "wiring-deploy", "deploy-repeat", "wiring-deploy-repeat", "canary-create",
            "backup", "remove", "wiring-remove", "deploy-fresh", "wiring-deploy-fresh",
            "canary-fresh", "backup-pre-restore", "restore-preview", "restore", "canary-verify",
            "deploy-final", "wiring-deploy-final"]

out = plan("tautulli")
if steps(out) != STATEFUL:
    failures.append(f"stateful sequence: {steps(out)}")
if "canary=marker" not in out:
    failures.append("Tautulli's drill.canary_path selects the marker canary")
if "expect=absent" not in out.split("wiring-remove", 1)[-1].splitlines()[0]:
    failures.append("the wiring check after Remove expects nothing left")
if "delete_data=true" not in out or "pre_restore_point=<backup-pre-restore-point>" not in out:
    failures.append("Remove deletes data and Restore passes the pre-restore point")

if "canary=hook" not in plan("influxdb"):
    failures.append("roles/influxdb/tasks/drill-canary.yml selects the hook canary")

radarr = plan("radarr")
if "role=servarr" not in radarr or "radarr-recovery.js" not in radarr:
    failures.append("a role alias finds the app's own browser spec")

stateless = plan("homepage")
if steps(stateless) != ["deploy", "wiring-deploy", "deploy-repeat", "wiring-deploy-repeat",
                        "remove", "wiring-remove", "deploy-fresh", "wiring-deploy-fresh",
                        "deploy-final", "wiring-deploy-final"]:
    failures.append(f"stateless sequence: {steps(stateless)}")

seeded = plan("--seed-wrong-point", "tautulli")
restore = [line for line in seeded.splitlines() if line.split()[1:2] and line.split()[1].endswith("-restore")
           and "restore-app.yml" in line]
if "backup-wrong-point" not in seeded or not restore \
        or "recovery_point=<backup-wrong-point-point>" not in restore[0] \
        or "pre_restore_point=<backup-pre-restore-point>" not in restore[0]:
    failures.append("--seed-wrong-point restores a fresh-install backup that is not the pre-restore point")
refused = subprocess.run([sys.executable, os.path.join(repo, "tools", "lab-drill"), "--plan",
                          "--seed-wrong-point", "homepage"], capture_output=True, text=True)
if refused.returncode != 2:
    failures.append("--seed-wrong-point refuses an app with nothing to restore")

for app in ("wordpress", "bookstack"):
    unrecoverable = subprocess.run([sys.executable, os.path.join(repo, "tools", "lab-drill"), "--plan",
                                    app], capture_output=True, text=True)
    if unrecoverable.returncode != 2 or "recovery.methods" not in unrecoverable.stderr:
        failures.append(f"{app}: backups without recovery.methods refuse the drill")

renamed = plan("--app", "radarr", "radarr-4k")
if "instance=radarr-4k" not in renamed or "app=radarr" not in renamed:
    failures.append("a renamed instance passes its app to every playbook")

for spec in ("mixpost", "wordpress"):
    text = open(os.path.join(repo, "ansible", "roles", spec, "browser", "recovery.js")).read()
    if "requested === 'fresh'" not in text:
        failures.append(f"{spec} browser spec accepts the drill's fresh mode")

# The instance's own backup.enabled, reported by check-wiring.yml, picks the sequence
# before anything destructive runs. A fake runner answers every playbook.
import tempfile
with tempfile.TemporaryDirectory() as lab:
    def drill(app, backup):
        runner = os.path.join(lab, "runner-lab-run.sh")
        with open(runner, "w") as handle:
            handle.write("#!/bin/sh\n"
                         f"case \"$2\" in *check-wiring*) echo 'WIRING x method=native-lxc backup={backup} "
                         "expect=present';; esac\n"
                         "echo 'HOMELAB_RUN result=SUCCEEDED changed=0 failed=0'\n")
        os.chmod(runner, 0o755)
        env = dict(os.environ, HOMELAB_LAB_DIR=lab, LAB_JOB_DIR=os.path.join(lab, "jobs"))
        return subprocess.run([sys.executable, os.path.join(repo, "tools", "lab-drill"), app],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
    off = drill("tautulli", "False")
    ran = [line.split()[1].split("-", 1)[1] for line in off.stdout.splitlines() if line.startswith("STEP ")]
    if off.returncode != 0 or "KIND stateless" not in off.stdout or ran[4:6] != ["remove", "wiring-remove"]:
        failures.append(f"an instance with backups off runs the stateless drill: {off.stdout[-400:]}")
    on = drill("homepage", "True")
    if on.returncode == 0 or "recovery.methods" not in on.stdout or "-remove " in on.stdout:
        failures.append(f"an instance with backups on and no recovery.methods stops before Remove: "
                        f"{on.stdout[-400:]}")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("lab-drill plans the stateful, stateless and seeded drills, its wiring checks and each "
      "app's canary")
