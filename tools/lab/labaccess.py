"""Shared lab access for the tools/lab commands.

Private values come from $HOMELAB_LAB_DIR/lab-values.yml at run time; nothing private
lives in this repository. Nodes are reached as root over SSH, the way runner-lab-run.sh
reaches them.
"""
import json
import os
import shlex
import subprocess
import sys

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def die(message, code=2):
    print(f"{os.path.basename(sys.argv[0])}: {message}", file=sys.stderr)
    sys.exit(code)


def lab_values():
    lab_dir = os.environ.get("HOMELAB_LAB_DIR")
    if not lab_dir:
        die("HOMELAB_LAB_DIR is not set")
    try:
        import yaml
    except ImportError:
        die("python3 yaml module is missing")
    with open(os.path.join(lab_dir, "lab-values.yml")) as handle:
        return yaml.safe_load(handle)["current"]


def node_ips(values):
    return [str(values[k]) for k in sorted(values) if k.startswith("pve-node-") and k.endswith("-ip")]


def ssh_ok(ip, command):
    return subprocess.run(SSH + [f"root@{ip}", command], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def any_node(values):
    for ip in node_ips(values):
        if ssh_ok(ip, "true"):
            return ip
    die("no lab node answers over SSH", 1)


def runner(values):
    """Return (node_ip, vmid) for the runner LXC."""
    vmid = str(values["runner-vmid"])
    for ip in node_ips(values):
        if ssh_ok(ip, f"pct status {vmid}"):
            return ip, vmid
    die(f"runner {vmid} is not on any current node", 1)


def pvesh_json(ip, path):
    out = subprocess.run(SSH + [f"root@{ip}", f"pvesh get {path} --output-format json"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if out.returncode != 0:
        die(f"pvesh get {path} failed on {ip}: {out.stderr.strip()}", 1)
    return json.loads(out.stdout)


def owned_guest(values, target):
    """Find a `_+lab` guest by vmid or exact name. Returns (node_ip, guest dict)."""
    ip = any_node(values)
    guests = [g for g in pvesh_json(ip, "/cluster/resources --type vm")
              if str(g.get("vmid")) == target or g.get("name") == target]
    if not guests:
        die(f"no guest named or numbered {target!r}", 1)
    if len(guests) > 1:
        die(f"{target!r} matches several guests: " + ", ".join(f"{g['vmid']} {g.get('name')}" for g in guests))
    guest = guests[0]
    if "_+lab" not in (guest.get("tags") or "").split(";"):
        die(f"guest {guest['vmid']} {guest.get('name')} is not tagged _+lab; refusing", 1)
    nodes = {n["name"]: n["ip"] for n in pvesh_json(ip, "/cluster/status") if n.get("type") == "node"}
    if guest["node"] not in nodes:
        die(f"node {guest['node']} has no address in /cluster/status", 1)
    return nodes[guest["node"]], guest


def command_or_stdin(args):
    """The command is the joined arguments, or stdin when there are none or it is `-`."""
    if not args or args == ["-"]:
        script = sys.stdin.buffer.read()
        if not script.strip():
            die("no command given and stdin is empty")
        return None, script
    return " ".join(args), None


def run_on_node(ip, remote, script):
    """Run `remote` on node `ip`; `script` (bytes) is piped to its stdin when given."""
    return subprocess.run(SSH + [f"root@{ip}", remote],
                          input=script, stdin=None if script is not None else subprocess.DEVNULL).returncode


def pct_exec(vmid, command):
    inner = "bash -s" if command is None else "bash -c " + shlex.quote(command)
    return f"pct exec {vmid} -- {inner}"
