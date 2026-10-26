#!/usr/bin/env python3
"""Render and reconcile the documented OPNsense base firewall example."""

import copy
import base64
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
    def _auth_role(self, path):
        expected = self.server.admin_auth if path.startswith("/api/core/backup/") else self.server.regular_auth
        authorization = self.headers.get("Authorization", "")
        role = "admin" if authorization == self.server.admin_auth else "regular"
        self.server.auth_log.append((path, role))
        return role if authorization == expected else None

    def do_GET(self):
        role = self._auth_role(self.path)
        self.server.calls.append("core/backup/backups")
        self.server.request_log.append(("GET", self.path, {}, role))
        if role != "admin":
            return self.reply(401, {})
        if self.path != "/api/core/backup/backups/this":
            return self.reply(404, {})
        self.server.backup_reads.append({
            "id": self.server.backup_id,
            "ids": [backup["id"] for backup in self.server.backup_history],
            "rules": copy.deepcopy(self.server.rules),
            "aliases": copy.deepcopy(self.server.aliases),
        })
        return self.reply(200, {"items": [{"id": backup["id"]}
                                             for backup in self.server.backup_history]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        parts = self.path.strip("/").split("/")
        if parts[:3] == ["api", "core", "backup"]:
            model, action = "backup", parts[3]
        else:
            model, action = parts[2], parts[3]
        self.server.calls.append(f"{model}/{action}")
        role = self._auth_role(self.path)
        self.server.request_log.append(("POST", self.path, body, role))
        if role is None:
            return self.reply(401, {})

        if model == "backup":
            backup = next((backup for backup in self.server.backup_history
                           if backup["id"] == parts[4]), None)
            if action != "revertBackup" or role != "admin" or backup is None:
                return self.reply(404, {})
            self.server.rules = copy.deepcopy(backup["rules"])
            self.server.aliases = copy.deepcopy(backup["aliases"])
            self.server.reverted = True
            return self.reply(200, {"status": "reverted"})

        if model == "alias":
            aliases = self.server.aliases
            if action == "searchItem":
                phrase = body.get("searchPhrase", "")
                rows = [dict(uuid=key, **value) for key, value in aliases.items()
                        if phrase in str(value.get("name", ""))]
                return self.reply(200, {"rows": rows, "total": len(rows)})
            if action == "addItem":
                aliases[str(uuid.uuid4())] = flatten(body["alias"])
                self.server.record_backup()
                return self.reply(200, {"result": "saved"})
            if action == "setItem":
                aliases[parts[4]] = flatten(body["alias"])
                self.server.record_backup()
                return self.reply(200, {"result": "saved"})
            if action == "reconfigure":
                self.server.active_aliases = copy.deepcopy(aliases)
                return self.reply(200, {"status": "ok"})
            return self.reply(404, {})

        if model != "filter":
            return self.reply(404, {})

        if action == "searchRule":
            if self.server.verify_failed_phase == "rules" and self.server.applied \
                    and body.get("searchPhrase") == "homelab-infra: base ":
                return self.reply(503, {"error": "unavailable"})
            if self.server.api_down_after_failure and self.server.applied \
                    and body.get("searchPhrase") == "homelab-infra: base":
                self.connection.close()
                return
            phrase = body.get("searchPhrase", "")
            rows = [dict(uuid=key, **value) for key, value in self.server.rules.items()
                    if phrase in str(value.get("description", ""))]
            if (self.server.legacy_verify_failed and self.server.legacy_delete_started
                    and self.server.applied and phrase == "Green lab: "):
                rows.append({"uuid": "legacy-readback-failure",
                             "description": "Green lab: injected readback mismatch"})
            rows.append({"description": "automatic rule", "is_automatic": True})
            return self.reply(200, {"rows": rows, "total": len(rows)})
        if action == "addRule":
            self.server.rules[str(uuid.uuid4())] = flatten(body["rule"])
            self.server.record_backup()
            return self.reply(200, {"result": "saved"})
        if action == "setRule":
            self.server.rules[parts[4]] = flatten(body["rule"])
            self.server.record_backup()
            return self.reply(200, {"result": "saved"})
        if action == "delRule":
            removed = self.server.rules.pop(parts[4], None)
            if removed and str(removed.get("description", "")).startswith("Green lab: "):
                self.server.legacy_delete_started = True
            self.server.record_backup()
            return self.reply(200, {"result": "deleted"})
        if action == "apply":
            self.server.applied = True
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
        "homelabinfra_vault": {"firewall-admin": {
            "api_key": "fixture-admin-key",
            "api_secret": "fixture-admin-secret",
        }},
        "fw_base_verify_retries": retries,
        "fw_base_verify_delay": delay,
        "fw_base_api_timeout": 1,
    }


def configure_server(server, rules, aliases, *, verify_failed_phase="", legacy_verify_failed=False,
                     api_down_after_failure=False):
    server.regular_auth = "Basic " + base64.b64encode(
        b"fixture-key:fixture-secret").decode()
    server.admin_auth = "Basic " + base64.b64encode(
        b"fixture-admin-key:fixture-admin-secret").decode()
    server.rules = copy.deepcopy(rules)
    server.aliases = copy.deepcopy(aliases)
    server.active_aliases = copy.deepcopy(aliases)
    server.backup_counter = 0
    server.backup_history = [{
        "id": "config-2026-10-26_00.00.00.xml",
        "rules": copy.deepcopy(rules),
        "aliases": copy.deepcopy(aliases),
    }]
    server.backup_id = server.backup_history[0]["id"]
    def record_backup():
        server.backup_counter += 1
        backup_id = f"config-2026-10-26_00.00.00-{server.backup_counter:04}.xml"
        server.backup_history.insert(0, {
            "id": backup_id,
            "rules": copy.deepcopy(server.rules),
            "aliases": copy.deepcopy(server.aliases),
        })
        server.backup_history = server.backup_history[:100]
        server.backup_id = server.backup_history[0]["id"]

    server.record_backup = record_backup
    server.calls = []
    server.request_log = []
    server.auth_log = []
    server.backup_reads = []
    server.verify_failed_phase = verify_failed_phase
    server.legacy_verify_failed = legacy_verify_failed
    server.legacy_delete_started = False
    server.api_down_after_failure = api_down_after_failure
    server.applied = False
    server.reverted = False


def model_write_requests(request_log):
    writes = {"/api/firewall/alias/addItem", "/api/firewall/filter/addRule"}
    write_prefixes = ("/api/firewall/alias/setItem/",
                      "/api/firewall/filter/setRule/",
                      "/api/firewall/filter/delRule/")
    return [request for request in request_log
            if request[0] == "POST" and
            (request[1] in writes or request[1].startswith(write_prefixes))]


def run_base(server, root, base, enabled=True, *, retries=12, delay=4, expect_failure=False):
    server.calls.clear()
    server.request_log.clear()
    server.auth_log.clear()
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
    check("fixture API secrets are not printed",
          any(secret in result.stdout + result.stderr
              for secret in ("fixture-secret", "fixture-admin-secret")), False)
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
        "example_clients", "example_resolvers", "example_v6_clients",
        "example_v6_target", "example_private"})
    check("documented firewall rule fields", set(base["rules"][0]), {
        "name", "interface", "source", "destination", "protocol", "ports", "action", "sequence"})

    doctor_base = copy.deepcopy(base)
    doctor_base["enabled"] = True
    doctor_result = config_doctor(doctor_base)
    check("config doctor accepts enabled documented base rules", doctor_result.returncode, 0)

    mixed_icmp = copy.deepcopy(doctor_base)
    mixed_icmp["rules"].append({
        "name": "example mixed-family ICMP",
        "interface": "lan",
        "source": "example_clients",
        "destination": "example_resolvers",
        "protocol": "ICMP",
        "ports": [],
        "action": "pass",
    })
    mixed_icmp_result = config_doctor(mixed_icmp)
    check("config doctor rejects mixed-family ICMP rules",
          mixed_icmp_result.returncode != 0, True)
    check("mixed-family ICMP diagnostic explains the restriction",
          "ICMP rules cannot mix IPv4 and IPv6 aliases"
          in mixed_icmp_result.stdout + mixed_icmp_result.stderr, True)

    initial_rules = {
        f"legacy-{index}": {"description": f"Green lab: migrated rule {index}"}
        for index in range(70)
    }
    initial_rules.update({
        "caddy-80": {"description": "Green lab: operator to Caddy port 80"},
        "caddy-443": {"description": "Green lab: operator to Caddy port 443"},
        "caddy-unrecognized": {
            "description": "Green lab: operator to Caddy unexpected legacy rule"},
        "manual": {"description": "operator hand test rule", "interface": "lan"},
        "app-rule": {"description": "homelab-infra: slskd egress peers", "interface": "opt4"},
    })
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
    configure_server(server, initial_rules, {
        "foreign": {"name": "hli_operator", "enabled": "1", "type": "host",
                    "content": "192.0.2.250", "description": "operator-owned"},
    })
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result, calls = run_base(server, root, base)
        check("first run creates five declared aliases",
              calls.count("alias/addItem"), 5)
        check("first run reloads aliases", calls.count("alias/reconfigure"), 1)
        check("first run creates five rendered filter rules",
              calls.count("filter/addRule"), 5)
        check("first run captures separate owned-rule and legacy-cleanup backups",
              calls.count("core/backup/backups"), 2)
        check("the second phase starts from a new config backup",
              len(server.backup_reads) == 2
              and server.backup_reads[0]["id"] != server.backup_reads[1]["id"], True)
        check("the first rollback ID stays in history through owned-rule writes",
              len(server.backup_reads) == 2
              and server.backup_reads[0]["id"] in server.backup_reads[1]["ids"], True)
        check("legacy Green lab rules are removed except the Caddy drill path",
              {key for key, rule in server.rules.items()
               if rule.get("description", "").startswith("Green lab: ")},
              {"caddy-80", "caddy-443"})
        check("manual test rule survives takeover",
              server.rules["manual"], {"description": "operator hand test rule", "interface": "lan"})
        check("app-owned firewall rule survives takeover",
              server.rules["app-rule"], {
                  "description": "homelab-infra: slskd egress peers", "interface": "opt4"})
        backup_index = next((i for i, request in enumerate(server.request_log)
                             if request[0] == "GET"
                             and request[1] == "/api/core/backup/backups/this"), -1)
        first_write_index = next((i for i, request in enumerate(server.request_log)
                                  if request[0] == "POST" and request[1] in {
                                      "/api/firewall/alias/addItem",
                                      "/api/firewall/alias/setItem/foreign",
                                      "/api/firewall/filter/addRule",
                                      "/api/firewall/filter/setRule/legacy-runner",
                                      "/api/firewall/filter/delRule/legacy-runner",
                                  }), -1)
        check("config backup is read before the first firewall write",
              backup_index >= 0 and first_write_index >= 0 and backup_index < first_write_index, True)
        apply_index = next((i for i, request in enumerate(server.request_log)
                            if request[1] == "/api/firewall/filter/apply"), -1)
        verify_index = next((i for i, request in enumerate(server.request_log)
                             if request[1] == "/api/firewall/filter/searchRule"
                             and request[2].get("searchPhrase") == "homelab-infra: base "
                             and i > apply_index), -1)
        check("apply is followed by an owned-rule readback", apply_index >= 0 and
              verify_index > apply_index, True)
        check("admin credential is used only for backup-list calls",
              [(path, role) for path, role in server.auth_log if role == "admin"],
              [("/api/core/backup/backups/this", "admin"),
               ("/api/core/backup/backups/this", "admin")])
        check("regular firewall credential is used for rule/apply APIs",
              all(role == "regular" for path, role in server.auth_log
                  if not path.startswith("/api/core/backup/")), True)
        backup_positions = [i for i, request in enumerate(server.request_log)
                            if request[0] == "GET"
                            and request[1] == "/api/core/backup/backups/this"]
        write_positions = [i for i, request in enumerate(server.request_log)
                           if request in model_write_requests([request])]
        phase_writes = [sum(start < i < end for i in write_positions)
                        for start, end in zip(backup_positions,
                                              backup_positions[1:] + [len(server.request_log)])]
        check("each migration phase stays below the 80-write backup-retention guard",
              phase_writes, [10, 71])

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
        check("IPv6 ICMP renders the IPv6 protocol and family",
              {key: rendered["homelab-infra: base example IPv6 probe"][key]
               for key in ("source_net", "destination_net", "protocol", "ipprotocol")}
              if "homelab-infra: base example IPv6 probe" in rendered else {}, {
                  "source_net": "hli_example_v6_clients",
                  "destination_net": "hli_example_v6_target",
                  "protocol": "IPv6-ICMP", "ipprotocol": "inet6",
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

        resolver_alias = next(value for value in server.aliases.values()
                              if value.get("name") == "hli_example_resolvers")
        resolver_alias["content"] = "2001:DB8::53\r\n192.0.2.53"
        result, repeat_calls = run_base(server, root, base)
        write_calls = [call for call in repeat_calls if call in (
            "alias/addItem", "alias/setItem", "alias/delItem", "alias/reconfigure",
            "filter/addRule", "filter/setRule", "filter/delRule", "filter/apply",
            "backup/revertBackup")]
        check("repeat run makes no writes or apply", write_calls, [])
        check("repeat run does not capture a rollback backup",
              "core/backup/backups" in repeat_calls, False)
        check("repeat run reports no changes", "changed=0" in result.stdout, True)
        check("OPNsense alias ordering and IPv6 case differences do not cause drift",
              "alias/setItem" in repeat_calls, False)

        overflow_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
        configure_server(overflow_server, {
            f"legacy-{index}": {"description": f"Green lab: migrated rule {index}"}
            for index in range(81)
        }, {})
        threading.Thread(target=overflow_server.serve_forever, daemon=True).start()
        _, overflow_calls = run_base(overflow_server, root, base, expect_failure=True)
        check("over-80 legacy writes are refused before backup or model writes",
              ("core/backup/backups" in overflow_calls,
               len(model_write_requests(overflow_server.request_log))), (False, 0))
        overflow_server.shutdown()

        disabled = copy.deepcopy(base)
        disabled["enabled"] = False
        _, disabled_calls = run_base(server, root, disabled, enabled=False)
        check("disabled base management makes no API calls", disabled_calls, [])

        failure_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
        configure_server(failure_server,
                         {"legacy": {"description": "Green lab: legacy"}}, {
                             "existing-clients": {
                                 "name": "hli_example_clients", "enabled": "1",
                                 "type": "host", "content": "192.0.2.99",
                                 "description": "homelab-infra: base alias example_clients",
                             },
                         },
                         verify_failed_phase="rules")
        threading.Thread(target=failure_server.serve_forever, daemon=True).start()
        failed_result, failed_calls = run_base(failure_server, root, base, retries=1, delay=0,
                                               expect_failure=True)
        check("failed readback reverts the captured backup",
              "backup/revertBackup" in failed_calls, True)
        check("failed readback reapplies the restored ruleset",
              failed_calls.count("filter/apply"), 2)
        revert_index = next((i for i, request in enumerate(failure_server.request_log)
                             if request[1].startswith("/api/core/backup/revertBackup/")), -1)
        restore_alias_index = next((i for i, request in enumerate(failure_server.request_log)
                                    if request[1] == "/api/firewall/alias/reconfigure"
                                    and i > revert_index), -1)
        restore_apply_index = next((i for i, request in enumerate(failure_server.request_log)
                                    if request[1] == "/api/firewall/filter/apply"
                                    and i > revert_index), -1)
        check("rollback reloads restored aliases after revert and before filter apply",
              revert_index >= 0 and revert_index < restore_alias_index < restore_apply_index,
              True)
        check("admin credential is used only for backup list and revert",
              [(path, role) for path, role in failure_server.auth_log if role == "admin"],
              [("/api/core/backup/backups/this", "admin"),
               (f"/api/core/backup/revertBackup/{failure_server.backup_reads[0]['id']}",
                "admin")])
        check("failed readback restores the full pre-write state",
              (failure_server.rules, failure_server.aliases),
              (failure_server.backup_reads[0]["rules"],
               failure_server.backup_reads[0]["aliases"]))
        check("failed readback reloads the restored alias table",
              failure_server.active_aliases,
              failure_server.backup_reads[0]["aliases"])
        check("failed readback reports that its backup was restored",
              ("was restored and applied" in failed_result.stdout + failed_result.stderr)
              and failure_server.reverted, True)
        failure_server.shutdown()

        legacy_failure_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
        legacy_rules = {
            f"legacy-{index}": {"description": f"Green lab: migrated rule {index}"}
            for index in range(70)
        }
        legacy_rules.update({
            "caddy": {"description": "Green lab: operator to Caddy port 443"},
            "manual": {"description": "operator hand test rule", "interface": "lan"},
        })
        configure_server(legacy_failure_server, legacy_rules, {}, legacy_verify_failed=True)
        threading.Thread(target=legacy_failure_server.serve_forever, daemon=True).start()
        failed_result, legacy_failure_calls = run_base(
            legacy_failure_server, root, base, retries=1, delay=0, expect_failure=True)
        check("legacy cleanup rollback uses its own pre-delete backup",
              [(path, role) for path, role in legacy_failure_server.auth_log
               if role == "admin" and "revertBackup" in path],
              [(f"/api/core/backup/revertBackup/"
                f"{legacy_failure_server.backup_reads[1]['id']}", "admin")])
        check("legacy cleanup rollback restores verified owned rules and original legacy rules",
              (legacy_failure_server.rules, legacy_failure_server.aliases),
              (legacy_failure_server.backup_reads[1]["rules"],
               legacy_failure_server.backup_reads[1]["aliases"]))
        check("legacy cleanup failure applies the restored snapshot and reports its ID",
              (legacy_failure_calls.count("filter/apply"),
               legacy_failure_server.reverted,
               legacy_failure_server.backup_reads[1]["id"]
               in failed_result.stdout + failed_result.stderr), (3, True, True))
        check("both phase backup IDs survive the bounded migration history",
              all(backup["id"] in [item["id"] for item in legacy_failure_server.backup_history]
                  for backup in legacy_failure_server.backup_reads), True)
        legacy_failure_server.shutdown()

        unreachable_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
        configure_server(unreachable_server,
                         {"legacy": {"description": "Green lab: legacy"}}, {},
                         api_down_after_failure=True)
        threading.Thread(target=unreachable_server.serve_forever, daemon=True).start()
        failed_result, unreachable_calls = run_base(
            unreachable_server, root, base, retries=1, delay=0, expect_failure=True)
        check("unreachable management API does not call revert",
              "backup/revertBackup" in unreachable_calls, False)
        check("unreachable management API failure names the backup ID",
              unreachable_server.backup_reads[0]["id"]
              in failed_result.stdout + failed_result.stderr, True)
        unreachable_server.shutdown()

    server.shutdown()
    if failures:
        raise SystemExit("\n".join(failures))
    print("firewall base rules and aliases reconcile safely from config.example")


if __name__ == "__main__":
    main()
