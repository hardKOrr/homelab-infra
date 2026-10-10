#!/usr/bin/env python3
"""Run the OPNsense firewall wiring and unwiring against a local fake of its API."""

import http.server
import json
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
failures = []


def check(name, actual, expected):
    if actual != expected:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")


def flatten(rule):
    flat = {}
    for key, value in rule.items():
        if isinstance(value, dict):
            for sub, inner in value.items():
                flat[f"{key}.{sub}"] = inner
        else:
            flat[key] = value
    return flat


class FakeOPNsense(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        parts = self.path.strip("/").split("/")  # api firewall <model> <action> [uuid]
        model, action = parts[2], parts[3]
        rules = self.server.rules[model]
        self.server.calls.append(f"{model}/{action}")
        if self.headers.get("Authorization") is None:
            return self.reply(401, {})
        if action == "searchRule":
            phrase = body.get("searchPhrase", "")
            rows = [dict(uuid=k, **v) for k, v in rules.items()
                    if any(phrase in str(x) for x in v.values())]
            rows.append({"description": "Automatic rule", "is_automatic": True})
            return self.reply(200, {"rows": rows, "total": len(rows)})
        if action == "addRule":
            rules[str(uuid.uuid4())] = flatten(body["rule"])
            return self.reply(200, {"result": "saved"})
        if action == "setRule":
            rules[parts[4]] = flatten(body["rule"])
            return self.reply(200, {"result": "saved"})
        if action == "delRule":
            rules.pop(parts[4])
            return self.reply(200, {"result": "deleted"})
        if action == "apply":
            return self.reply(200, {"status": "OK\n"})
        return self.reply(404, {})

    def reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *_args):
        pass


def run(server, root, task, instance, firewall):
    server.calls.clear()
    infra = {"firewall": {
        "provider": "opnsense", "host": f"http://127.0.0.1:{server.server_port}",
        "api_key": "fixture-key", "api_secret": "fixture-secret",
        "interfaces": [{"cidr": "198.51.100.0/24", "interface": "opt4"},
                       {"cidr": "203.0.113.0/24", "interface": "opt8"}]}}
    play = [{
        "hosts": "localhost", "gather_facts": False,
        "vars": {"homelabinfra_infra": infra, "wiring_app_name": instance,
                 "wiring_upstream_host": "198.51.100.34", "wiring_firewall": firewall},
        "tasks": [{"ansible.builtin.include_tasks": str(repo / "ansible/tasks" / task)}],
    }]
    path = root / "play.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        failures.append(f"{task} for {instance} failed:\n{result.stdout[-3000:]}")
    return [c for c in server.calls if not c.endswith("searchRule")]


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
    server.rules = {"filter": {}, "d_nat": {}}
    server.calls = []
    foreign = {"description": "Green lab: VLAN 40 packages", "interface": "opt4"}
    sibling = {"description": "homelab-infra: slskd-2 egress soulseek", "interface": "opt4"}
    server.rules["filter"].update({"foreign": foreign, "sibling": sibling})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    declared = {"egress": [{"name": "soulseek", "protocol": "TCP", "port": ""}],
                "inbound": [{"name": "peers", "protocol": "TCP", "port": 50300,
                             "enabled": False}]}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        wire, unwire = "wiring/firewall-opnsense.yml", "unwiring/firewall-opnsense.yml"

        check("first deploy adds one egress rule and applies",
              run(server, root, wire, "slskd", declared), ["filter/addRule", "filter/apply"])
        owned = [r for r in server.rules["filter"].values()
                 if r["description"] == "homelab-infra: slskd egress soulseek"]
        check("egress rule shape", owned and {k: owned[0][k] for k in (
            "interface", "source_net", "destination_net", "destination_port",
            "protocol", "action", "sequence")}, {
            "interface": "opt4", "source_net": "198.51.100.34", "destination_net": "any",
            "destination_port": "", "protocol": "TCP", "action": "pass", "sequence": "1000"})
        check("disabled forward publishes nothing", server.rules["d_nat"], {})

        check("second deploy changes nothing",
              run(server, root, wire, "slskd", declared), [])

        owned[0]["destination_port"] = "2271"
        check("drift is corrected in place",
              run(server, root, wire, "slskd", declared), ["filter/setRule", "filter/apply"])

        declared["inbound"][0]["enabled"] = True
        check("enabled forward is published",
              run(server, root, wire, "slskd", declared), ["d_nat/addRule", "filter/apply"])
        forward = next(iter(server.rules["d_nat"].values()))
        check("forward shape", {k: forward[k] for k in (
            "interface", "destination.network", "destination.port", "target",
            "local-port", "protocol", "pass")}, {
            "interface": "wan", "destination.network": "wanip", "destination.port": "50300",
            "target": "198.51.100.34", "local-port": "50300", "protocol": "tcp",
            "pass": "pass"})

        declared["inbound"][0]["enabled"] = False
        check("disabling the forward withdraws it",
              run(server, root, wire, "slskd", declared), ["d_nat/delRule", "filter/apply"])

        check("remove withdraws only this instance's rules",
              run(server, root, unwire, "slskd", {}), ["filter/delRule", "filter/apply"])
        check("foreign and sibling-instance rules survive",
              sorted(server.rules["filter"]), ["foreign", "sibling"])
        check("second remove changes nothing", run(server, root, unwire, "slskd", {}), [])
    server.shutdown()

    for failure in failures:
        print("FAIL:", failure)
    print("firewall wiring: %s" % ("FAILED" if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
