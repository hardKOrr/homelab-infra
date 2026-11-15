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


STATEFUL = ["deploy", "deploy-repeat", "canary-create", "backup", "remove", "deploy-fresh",
            "canary-fresh", "backup-pre-restore", "restore-preview", "restore", "canary-verify",
            "deploy-final"]

out = plan("tautulli")
if steps(out) != STATEFUL:
    failures.append(f"stateful sequence: {steps(out)}")
if "canary=marker" not in out:
    failures.append("Tautulli's drill.canary_path selects the marker canary")
if "delete_data=true" not in out or "pre_restore_point=<backup-pre-restore-point>" not in out:
    failures.append("Remove deletes data and Restore passes the pre-restore point")

if "canary=hook" not in plan("influxdb"):
    failures.append("roles/influxdb/tasks/drill-canary.yml selects the hook canary")

radarr = plan("radarr")
if "role=servarr" not in radarr or "radarr-recovery.js" not in radarr:
    failures.append("a role alias finds the app's own browser spec")

stateless = plan("homepage")
if steps(stateless) != ["deploy", "deploy-repeat", "remove", "deploy-fresh", "deploy-final"]:
    failures.append(f"stateless sequence: {steps(stateless)}")

renamed = plan("--app", "radarr", "radarr-4k")
if "instance=radarr-4k" not in renamed or "app=radarr" not in renamed:
    failures.append("a renamed instance passes its app to every playbook")

for spec in ("mixpost", "wordpress"):
    text = open(os.path.join(repo, "ansible", "roles", spec, "browser", "recovery.js")).read()
    if "requested === 'fresh'" not in text:
        failures.append(f"{spec} browser spec accepts the drill's fresh mode")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("lab-drill plans the stateful and stateless drills and picks each app's canary")
