#!/usr/bin/env python3
"""Render and reconcile the documented OPNsense base firewall example."""

import copy
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import uuid

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
python = Path.home() / ".venvs/homelab-ansible/bin/python"
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
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        parts = self.path.strip("/").split("/")
        model = parts[2]
        action = parts[3]
        self.server.calls.append(f"{model}/{action}")
        if self.headers.get("Authorization") is None:
            return self.reply(401, {})

        if model == "alias":
            aliases = self.server.aliases
            if action == "searchItem":
                phrase = body.get("searchPhrase", "")
                rows = [dict(uuid=key, **value) for key, value in aliases.items()
                        if phrase in str(value.get("name", ""))]
                return self.reply(200, {"rows": rows, "total": len(rows)})
            if action == "addItem":
                aliases[str(uuid.uuid4())] = flatten(body["alias"])
                return self.reply(200, {"result": "saved"})
            if action == "setItem":
                aliases[parts[4]] = flatten(body["alias"])
                return self.reply(200, {"result": "saved"})
            if action == "reconfigure":
                return self.reply(200, {"status": "ok"})
            return self.reply(404, {})

        if model != "filter":
            return self.reply(404, {})

        if action == "searchRule":
            if self.server.verify_failed and self.server.applied \
                    and body.get("searchPhrase") == "homelab-infra: base":
                return self.reply(503, {"error": "unavailable"})
            phrase = body.get("searchPhrase", "")
            rows = [dict(uuid=key, **value) for key, value in self.server.rules.items()
                    if phrase in str(value.get("description", ""))]
            rows.append({"description": "automatic rule", "is_automatic": True})
            return self.reply(200, {"rows": rows, "total": len(rows)})
        if action == "addRule":
            self.server.rules[str(uuid.uuid4())] = flatten(body["rule"])
            return self.reply(200, {"result": "saved"})
        if action == "setRule":
            self.server.rules[parts[4]] = flatten(body["rule"])
            return self.reply(200, {"result": "saved"})
        if action == "delRule":
            self.server.rules.pop(parts[4], None)
            return self.reply(200, {"result": "deleted"})
        if action == "savepoint":
            if self.server.savepoint_missing:
                return self.reply(404, {"errorMessage": "Endpoint not found"})
            self.server.armed_revision = "fixture-revision"
            return self.reply(200, {"status": "ok", "revision": self.server.armed_revision})
        if action == "apply":
            self.server.applied = True
            return self.reply(200, {"status": "OK"})
        if action == "cancelRollback":
            self.server.armed_revision = None
            return self.reply(200, {"status": "OK"})
        return self.reply(404, {})

    def reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *_args):
        pass


def play_config(server, base, enabled=True, *, retries=12, delay=4):
    config = copy.deepcopy(base)
    config["enabled"] = enabled
    firewall = copy.deepcopy(config)
    return {
        "homelabinfra_config": {"infrastructure": {"firewall": {
            "provider": "opnsense", "base": firewall}}},
        "homelabinfra_infra": {"firewall": {
            "provider": "opnsense",
            "host": f"http://127.0.0.1:{server.server_port}",
            "api_key": "fixture-key",
            "api_secret": "fixture-secret",
            "validate_certs": False,
        }},
        "fw_base_verify_retries": retries,
        "fw_base_verify_delay": delay,
    }


def run_base(server, root, base, enabled=True, *, retries=12, delay=4, expect_failure=False):
    server.calls.clear()
    vars_ = play_config(server, base, enabled, retries=retries, delay=delay)
    play = [{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": vars_,
        "tasks": [{
            "ansible.builtin.include_tasks": str(repo / "ansible/tasks/firewall/opnsense-base.yml"),
            "when": "homelabinfra_config.infrastructure.firewall.base.enabled | default(false) | bool",
        }],
    }]
    path = root / "base.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
        capture_output=True, text=True, check=False,
        env={**os.environ, "ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg")},
    )
    failed = result.returncode != 0
    if failed != expect_failure:
        failures.append(f"base reconciliation returned {result.returncode}:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}")
    check("fixture API secret is not printed", "fixture-secret" in result.stdout + result.stderr, False)
    return result, list(server.calls)


def config_doctor(base):
    config = yaml.safe_load((repo / "gate/fixtures/config-loading/valid/infrastructure.yml").read_text())
    config["firewall"] = {
        "provider": "opnsense",
        "host": "203.0.113.1",
        "interfaces": [{"cidr": "198.51.100.0/24", "interface": "lan"}],
        "base": copy.deepcopy(base),
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "infrastructure.yml").write_text(yaml.safe_dump(config))
        (root / "proxmox.yml").write_text(
            (repo / "gate/fixtures/config-loading/valid/proxmox.yml").read_text())
        return subprocess.run(
            ["bash", str(repo / "ansible/scripts/config-doctor.sh"), str(root)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg")},
        )


def main():
    example = yaml.safe_load((repo / "config.example/infrastructure.yml").read_text())
    base = example["firewall"]["base"]
    check("documented base rules default to disabled", base["enabled"], False)
    check("documented aliases are loaded for rendering", set(base["aliases"]), {
        "example_clients", "example_resolvers", "example_private"})
    check("documented firewall rule fields", set(base["rules"][0]), {
        "name", "interface", "source", "destination", "protocol", "ports", "action", "sequence"})

    doctor_base = copy.deepcopy(base)
    doctor_base["enabled"] = True
    doctor_result = config_doctor(doctor_base)
    check("config doctor accepts enabled documented base rules", doctor_result.returncode, 0)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
    server.rules = {
        "legacy-runner": {"description": "Green lab: runner to Proxmox"},
        "legacy-vlan": {"description": "Green lab: VLAN 20 packages"},
        "caddy-80": {"description": "Green lab: operator to Caddy port 80"},
        "caddy-443": {"description": "Green lab: operator to Caddy port 443"},
        "manual": {"description": "operator hand test rule", "interface": "lan"},
        "app-rule": {"description": "homelab-infra: slskd egress peers", "interface": "opt4"},
    }
    server.aliases = {
        "foreign": {"name": "hli_operator", "enabled": "1", "type": "host",
                    "content": "192.0.2.250", "description": "operator-owned"},
    }
    server.calls = []
    server.verify_failed = False
    server.savepoint_missing = False
    server.applied = False
    server.armed_revision = None
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result, calls = run_base(server, root, base)
        check("first run creates three declared aliases",
              calls.count("alias/addItem"), 3)
        check("first run reloads aliases", calls.count("alias/reconfigure"), 1)
        check("first run creates four rendered filter rules",
              calls.count("filter/addRule"), 4)
        check("legacy Green lab rules are removed except the Caddy drill path",
              {key for key, rule in server.rules.items()
               if rule.get("description", "").startswith("Green lab: ")},
              {"caddy-80", "caddy-443"})
        check("manual test rule survives takeover",
              server.rules["manual"], {"description": "operator hand test rule", "interface": "lan"})
        check("app-owned firewall rule survives takeover",
              server.rules["app-rule"], {
                  "description": "homelab-infra: slskd egress peers", "interface": "opt4"})
        savepoint_index = calls.index("filter/savepoint") if "filter/savepoint" in calls else -1
        alias_write_index = calls.index("alias/addItem") if "alias/addItem" in calls else -1
        check("savepoint precedes all writes",
              savepoint_index >= 0 and alias_write_index >= 0 and savepoint_index < alias_write_index, True)
        if "filter/apply" in calls and "filter/cancelRollback" in calls:
            apply_index = calls.index("filter/apply")
            verify_index = next((i for i in range(apply_index + 1, len(calls))
                                 if calls[i] == "filter/searchRule"), -1)
            confirm_index = calls.index("filter/cancelRollback")
            check("apply, API verification and confirmation follow writes",
                  apply_index < verify_index < confirm_index, True)
        else:
            check("apply, API verification and confirmation follow writes", False, True)
        check("confirmed apply disarms the rollback", server.armed_revision, None)

        aliases = {value["name"]: value for value in server.aliases.values()}
        check("documented host alias renders its address",
              aliases.get("hli_example_clients", {}).get("content"),
              "192.0.2.20")
        check("documented resolver alias renders both address families",
              aliases.get("hli_example_resolvers"), {
            "name": "hli_example_resolvers", "enabled": "1", "type": "host",
            "content": "192.0.2.53\n2001:db8::53",
            "description": "homelab-infra: base alias example_resolvers",
        })
        check("documented network alias is a network alias",
              aliases.get("hli_example_private", {}).get("type"), "network")

        rendered = {rule.get("description"): rule for rule in server.rules.values()
                    if rule.get("description", "").startswith("homelab-infra: base ")}
        check("single-port DNS renders its declared alias and protocol",
              {key: rendered["homelab-infra: base example client DNS"][key]
               for key in ("source_net", "destination_net", "protocol", "destination_port", "sequence")}
              if "homelab-infra: base example client DNS" in rendered else {}, {
                  "source_net": "hli_example_clients", "destination_net": "hli_example_resolvers",
                  "protocol": "TCP/UDP", "destination_port": "53", "sequence": "230",
              })
        check("multi-port web rule expands to unique descriptions", sorted(
            key for key in rendered if key.startswith("homelab-infra: base example client web")), [
                "homelab-infra: base example client web port 443",
                "homelab-infra: base example client web port 80",
            ])
        check("private deny renders with block action before web allow priority",
              {key: rendered["homelab-infra: base example private deny"][key]
               for key in ("action", "destination_net", "sequence")}
              if "homelab-infra: base example private deny" in rendered else {}, {
                  "action": "block", "destination_net": "hli_example_private", "sequence": "300",
              })

        result, repeat_calls = run_base(server, root, base)
        write_calls = [call for call in repeat_calls if call in (
            "alias/addItem", "alias/setItem", "alias/delItem", "alias/reconfigure",
            "filter/addRule", "filter/setRule", "filter/delRule", "filter/savepoint",
            "filter/apply", "filter/cancelRollback")]
        check("repeat run makes no writes or apply", write_calls, [])
        check("repeat run reports no changes", "changed=0" in result.stdout, True)

        disabled = copy.deepcopy(base)
        disabled["enabled"] = False
        _, disabled_calls = run_base(server, root, disabled, enabled=False)
        check("disabled base management makes no API calls", disabled_calls, [])

        failure_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
        failure_server.rules = {"legacy": {"description": "Green lab: legacy"}}
        failure_server.aliases = {}
        failure_server.calls = []
        failure_server.verify_failed = True
        failure_server.savepoint_missing = False
        failure_server.applied = False
        failure_server.armed_revision = None
        threading.Thread(target=failure_server.serve_forever, daemon=True).start()
        _, failed_calls = run_base(failure_server, root, base, retries=1, delay=0, expect_failure=True)
        check("failed management verification leaves rollback armed",
              failure_server.armed_revision, "fixture-revision")
        check("failed management verification does not confirm rollback",
              "filter/cancelRollback" in failed_calls, False)
        failure_server.shutdown()

        missing_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
        missing_server.rules = {"legacy": {"description": "Green lab: legacy"}}
        missing_server.aliases = {}
        missing_server.calls = []
        missing_server.verify_failed = False
        missing_server.savepoint_missing = True
        missing_server.applied = False
        missing_server.armed_revision = None
        threading.Thread(target=missing_server.serve_forever, daemon=True).start()
        _, missing_calls = run_base(missing_server, root, base, expect_failure=True)
        write_calls = [call for call in missing_calls if call in (
            "alias/addItem", "filter/addRule", "filter/delRule", "filter/apply")]
        check("missing savepoint endpoint prevents all firewall writes", write_calls, [])
        missing_server.shutdown()

    server.shutdown()
    if failures:
        raise SystemExit("\n".join(failures))
    print("firewall base rules and aliases reconcile safely from config.example")


if __name__ == "__main__":
    main()
