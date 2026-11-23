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


STATEFUL = ["deploy", "wiring-deploy", "canary-create", "deploy-repeat", "wiring-deploy-repeat",
            "backup", "remove", "wiring-remove", "deploy-fresh", "wiring-deploy-fresh",
            "canary-fresh", "backup-pre-restore", "restore-preview", "restore", "canary-verify",
            "deploy-final", "wiring-deploy-final", "canary-final"]

out = plan("tautulli")
if steps(out) != ["requires-plex"] + STATEFUL:
    failures.append(f"stateful sequence: {steps(out)}")
immich = steps(plan("immich"))
if immich[:3] != ["backend-postgresql-immich", "backend-redis-immich", "deploy"]:
    failures.append(f"the app's named backends deploy before the app: {immich[:3]}")
if "canary=marker" not in out:
    failures.append("Tautulli's drill.canary_path selects the marker canary")
if "expect=absent" not in out.split("wiring-remove", 1)[-1].splitlines()[0]:
    failures.append("the wiring check after Remove expects nothing left")
if "delete_data=true" not in out or "pre_restore_point=<backup-pre-restore-point>" not in out:
    failures.append("Remove deletes data and Restore passes the pre-restore point")

if "canary=marker {'native': '/etc/wireguard'" not in plan("wireguard"):
    failures.append("WireGuard's drill.canary_path maps each hosting method to a marker path")

if "canary=hook" not in plan("influxdb"):
    failures.append("roles/influxdb/tasks/drill-canary.yml selects the hook canary")

radarr = plan("radarr")
if "role=servarr" not in radarr or "radarr-recovery.js" not in radarr:
    failures.append("a role alias finds the app's own browser spec")
if steps(radarr)[2:4] != ["canary-owner", "canary-create"] or steps(radarr).count("canary-owner") != 1:
    failures.append("a forward_auth browser canary makes its Authentik drill owner once, first")
if "canary-owner" in steps(plan("mixpost")):
    failures.append("only a forward_auth route's drill owner is an Authentik account")

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

final = [line for line in out.splitlines() if "-canary-final " in line]
if not final or "drill_action=verify" not in final[0] or "drill_final=true" not in final[0]:
    failures.append("the last canary check runs verify with drill_final after the final Deploy")
for app in ("wordpress", "bookstack"):
    if " stateful " not in plan(app).splitlines()[0]:
        failures.append(f"{app}: drills as stateful through Backup App and Restore App")
# No catalog app is refused today; the rule itself stays covered.
from importlib.machinery import SourceFileLoader
lab_drill = SourceFileLoader("lab_drill", os.path.join(repo, "tools", "lab-drill")).load_module()
kind, refusal = lab_drill.kind_of("example", {"backup": {"enabled": True}}, True)
if kind is not None or "recovery.methods" not in (refusal or ""):
    failures.append("backups without recovery.methods refuse the drill")

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
    if off.returncode != 0 or "KIND stateless" not in off.stdout or ran[3:7] != [
            "deploy-repeat", "wiring-deploy-repeat", "remove", "wiring-remove"]:
        failures.append(f"an instance with backups off runs the stateless drill: {off.stdout[-400:]}")
    wordpress = drill("wordpress", "False")
    if wordpress.returncode != 0 or "KIND stateless" not in wordpress.stdout:
        failures.append(f"a WordPress instance with backups off runs the stateless drill: "
                        f"{wordpress.stdout[-300:]}{wordpress.stderr[-300:]}")
    # Seeded: verify fails on the wrong point; the drill passes only when the fresh check
    # that follows succeeds, showing the app answered and the canary is absent.
    def seeded(transport_down):
        runner = os.path.join(lab, "runner-lab-run.sh")
        verified = os.path.join(lab, "verified")
        if os.path.exists(verified):
            os.remove(verified)
        with open(runner, "w") as handle:
            handle.write("#!/bin/sh\n"
                         "case \"$*\" in\n"
                         "  *check-wiring*) echo 'WIRING x method=native-lxc backup=True expect=present';;\n"
                         f"  *drill_action=verify*) touch {verified}; exit 2;;\n"
                         f"  *drill_action=fresh*) {'[ -e ' + verified + ' ] && exit 2' if transport_down else ':'};;\n"
                         "  *backup-app*) echo \"host/x/$(date -u +%Y-%m-%dT%H:%M:%SZ)\";;\n"
                         "esac\n"
                         "echo 'HOMELAB_RUN result=SUCCEEDED changed=0 failed=0'\n")
        os.chmod(runner, 0o755)
        env = dict(os.environ, HOMELAB_LAB_DIR=lab, LAB_JOB_DIR=os.path.join(lab, "jobs"))
        return subprocess.run([sys.executable, os.path.join(repo, "tools", "lab-drill"),
                               "--seed-wrong-point", "tautulli"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
    caught = seeded(False)
    if caught.returncode != 0 or "as seeded" not in caught.stdout or "canary-absent" not in caught.stdout:
        failures.append(f"a seeded drill passes when verify fails and the canary is absent: "
                        f"{caught.stdout[-400:]}")
    broken = seeded(True)
    if broken.returncode == 0 or "as seeded" in broken.stdout:
        failures.append("a seeded drill fails when the app cannot be checked at all")
    # A hooked Deploy that fails (its claim token expired while a guest was built) gets a
    # fresh hook run and one retry; a Deploy without a hook fails at once.
    hooks = os.path.join(lab, "drill-hooks")
    os.makedirs(hooks, exist_ok=True)
    hook = os.path.join(hooks, "plex-pre-deploy")
    with open(hook, "w") as handle:
        handle.write(f"#!/bin/sh\necho x >> {lab}/hook-runs\n")
    os.chmod(hook, 0o755)
    runner = os.path.join(lab, "runner-lab-run.sh")
    failed_once = os.path.join(lab, "failed-once")
    with open(runner, "w") as handle:
        handle.write("#!/bin/sh\n"
                     "case \"$*\" in\n"
                     f"  *apps/plex.yml*) [ -e {failed_once} ] || {{ touch {failed_once}; exit 2; }};;\n"
                     "  *check-wiring*) echo 'WIRING x method=native-lxc backup=False expect=present';;\n"
                     "esac\n"
                     "echo 'HOMELAB_RUN result=SUCCEEDED changed=0 failed=0'\n")
    os.chmod(runner, 0o755)
    env = dict(os.environ, HOMELAB_LAB_DIR=lab, LAB_JOB_DIR=os.path.join(lab, "jobs"))
    retried = subprocess.run([sys.executable, os.path.join(repo, "tools", "lab-drill"), "tautulli"],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
    if "requires-plex-retry PASS" not in retried.stdout \
            or open(os.path.join(lab, "hook-runs")).read().count("x") < 2:
        failures.append(f"a failed hooked Deploy reruns its hook and retries once: {retried.stdout[-400:]}")
    os.remove(hook)

    on = drill("homepage", "True")
    if on.returncode == 0 or "recovery.methods" not in on.stdout or "-remove " in on.stdout:
        failures.append(f"an instance with backups on and no recovery.methods stops before Remove: "
                        f"{on.stdout[-400:]}")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("lab-drill plans the stateful, stateless and seeded drills, its wiring checks and each "
      "app's canary")
