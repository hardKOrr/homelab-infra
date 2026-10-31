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
        self.server.calls.append(f"{model}/{action}")
        if self.headers.get("Authorization") is None:
            return self.reply(401, {})
        if model == "alias":
            aliases = self.server.aliases
            if action == "searchItem":
                phrase = body.get("searchPhrase", "")
                rows = [dict(uuid=k, **v) for k, v in aliases.items()
                        if phrase in str(v.get("name", ""))]
                return self.reply(200, {"rows": rows, "total": len(rows)})
            if action == "addItem":
                aliases[str(uuid.uuid4())] = flatten(body["alias"])
                return self.reply(200, {"result": "saved"})
            if action == "setItem":
                aliases[parts[4]] = flatten(body["alias"])
                return self.reply(200, {"result": "saved"})
            if action == "delItem":
                aliases.pop(parts[4], None)
                return self.reply(200, {"result": "deleted"})
            if action == "reconfigure":
                return self.reply(200, {"status": "ok"})
            return self.reply(404, {})

        rules = self.server.rules[model]
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


def run(server, root, task, instance, firewall, extra_vars=None):
    server.calls.clear()
    infra = {"firewall": {
        "provider": "opnsense", "host": f"http://127.0.0.1:{server.server_port}",
        "api_key": "fixture-key", "api_secret": "fixture-secret",
        "interfaces": [{"cidr": "198.51.100.0/24", "interface": "opt4"},
                       {"cidr": "203.0.113.0/24", "interface": "opt8"}]}}
    play = [{
        "hosts": "localhost", "gather_facts": False,
        "vars": {"homelabinfra_infra": infra, "wiring_app_name": instance,
                 "wiring_upstream_host": "198.51.100.34", "wiring_firewall": firewall,
                 **(extra_vars or {})},
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


def run_k3s_alias(server, root, alias_name, state, nodes, *, expect_failure=False):
    server.calls.clear()
    infra = {"firewall": {
        "provider": "opnsense", "host": f"http://127.0.0.1:{server.server_port}",
        "api_key": "fixture-key", "api_secret": "fixture-secret"}}
    play = [{
        "hosts": "localhost", "gather_facts": False,
        "vars": {"homelabinfra_infra": infra,
                 "firewall_k3s_alias_state": state,
                 "firewall_k3s_alias_name": alias_name,
                 "firewall_k3s_cluster_name": "k3s-cluster",
                 "firewall_k3s_nodes": nodes},
        "tasks": [{"ansible.builtin.include_tasks": str(
            repo / "ansible/tasks/firewall/k3s-cluster-alias.yml")}],
    }]
    path = root / "k3s-alias.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
        capture_output=True, text=True, check=False)
    failed = result.returncode != 0
    if failed != expect_failure:
        failures.append(f"k3s alias {state} returned {result.returncode}:\n{result.stdout[-3000:]}")
    if expect_failure:
        check("foreign alias refusal is explained",
              "Refusing to change OPNsense alias" in result.stdout, True)
        check("alias API failure output is scrubbed",
              "fixture-secret" not in result.stdout + result.stderr, True)
    return [c for c in server.calls if c not in ("alias/searchItem",)]


def run_kubernetes_app_access(server, root, instance, firewall):
    server.calls.clear()
    infra = {"firewall": {
        "provider": "opnsense", "host": f"http://127.0.0.1:{server.server_port}",
        "api_key": "fixture-key", "api_secret": "fixture-secret",
        "interfaces": [
            {"cidr": "198.51.100.0/24", "interface": "opt4"},
            {"cidr": "203.0.113.0/24", "interface": "opt5"}]},
        "kubernetes": {"instance": "k3s-cluster",
                        "firewall_alias": "homelabinfra_k3s_k3s_cluster",
                        "ingress_vip": "203.0.113.34",
                        "nodes": [
                            {"name": "k3s-1", "ip_address": "198.51.100.21"},
                            {"name": "k3s-3", "ip_address": "198.51.100.23"}]}}
    play = [{
        "hosts": "localhost", "gather_facts": False,
        "vars": {"homelabinfra_infra": infra, "instance": instance,
                 "app_config": {"firewall": firewall}},
        "tasks": [{"ansible.builtin.include_tasks": str(
            repo / "ansible/tasks/kubernetes/open-app-access.yml")}],
    }]
    path = root / "kubernetes-app-firewall.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        failures.append(f"Kubernetes app firewall failed:\n{result.stdout[-3000:]}")
    return [c for c in server.calls if not c.endswith("searchRule")]


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
    server.rules = {"filter": {}, "d_nat": {}}
    server.aliases = {}
    server.calls = []
    foreign = {"description": "Green lab: VLAN 40 packages", "interface": "opt4"}
    sibling = {"description": "homelab-infra: slskd-2 egress soulseek", "interface": "opt4"}
    server.rules["filter"].update({"foreign": foreign, "sibling": sibling})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    declared = {"egress": [{"name": "soulseek", "protocol": "TCP", "port": ""},
                           {"name": "peer-range", "protocol": "TCP", "port": "",
                            "source_port": "61000-65535", "sequence": 750}],
                "inbound": [{"name": "peers", "protocol": "TCP", "port": 50300,
                             "enabled": False}]}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        wire, unwire = "wiring/firewall-opnsense.yml", "unwiring/firewall-opnsense.yml"

        alias_name = "homelabinfra_k3s_k3s_cluster"
        alias_nodes = [
            {"name": "k3s-1", "ip_address": "198.51.100.21"},
            {"name": "k3s-2", "ip_address": "198.51.100.22"},
        ]
        check("first cluster deploy creates and reloads its host alias",
              run_k3s_alias(server, root, alias_name, "present", alias_nodes),
              ["alias/addItem", "alias/reconfigure"])
        alias = next(iter(server.aliases.values()))
        check("cluster alias type, node list and ownership marker",
              {key: alias[key] for key in ("name", "type", "content", "description")}, {
                  "name": alias_name, "type": "host",
                  "content": "198.51.100.21\n198.51.100.22",
                  "description": "homelab-infra: k3s k3s-cluster"})
        check("unchanged cluster deploy makes no API write",
              run_k3s_alias(server, root, alias_name, "present", alias_nodes), [])
        check("cluster Remove alias preflight is read-only",
              run_k3s_alias(server, root, alias_name, "verify", []), [])

        alias_nodes.append({"name": "k3s-3", "ip_address": "198.51.100.23"})
        check("adding a cluster node updates the alias",
              run_k3s_alias(server, root, alias_name, "present", alias_nodes),
              ["alias/setItem", "alias/reconfigure"])
        check("added node reaches the host alias",
              next(iter(server.aliases.values()))["content"],
              "198.51.100.21\n198.51.100.22\n198.51.100.23")

        alias_nodes.pop(1)
        check("removing a cluster node updates the alias",
              run_k3s_alias(server, root, alias_name, "present", alias_nodes),
              ["alias/setItem", "alias/reconfigure"])
        check("removed node leaves the host alias",
              next(iter(server.aliases.values()))["content"],
              "198.51.100.21\n198.51.100.23")

        check("Kubernetes app deploy opens declared access through the alias",
              run_kubernetes_app_access(server, root, "kube-app", declared),
              ["alias/searchItem", "filter/addRule", "filter/addRule", "filter/apply"])
        kube_rules = [r for r in server.rules["filter"].values()
                      if r.get("description", "").startswith("homelab-infra: kube-app ")]
        check("Kubernetes app egress source is the cluster alias",
              {r["source_net"] for r in kube_rules}, {alias_name})
        check("Kubernetes app egress selects the interface from its node network",
              {r["interface"] for r in kube_rules}, {"opt4"})
        k8s_playbooks = ["flaresolverr", "hi-events", "homepage", "jellyseerr",
                         "kometa", "litellm", "maintainerr", "mixpost", "searxng"]
        for app in k8s_playbooks:
            plays = yaml.safe_load((repo / f"ansible/playbooks/apps/{app}.yml").read_text())
            has_firewall_adapter = any(
                task.get("ansible.builtin.include_tasks")
                == "../../tasks/kubernetes/open-app-access.yml"
                for play in plays for task in play.get("tasks", [])
                if isinstance(task, dict))
            check(f"{app} Deploy includes the Kubernetes firewall adapter",
                  has_firewall_adapter, True)
        check("Kubernetes app remove withdraws rules and leaves the alias",
              run(server, root, unwire, "kube-app", {}),
              ["filter/delRule", "filter/delRule", "filter/apply"])
        check("app removal preserves the cluster host alias",
              {row["name"] for row in server.aliases.values()}, {alias_name})

        check("cluster removal deletes and reloads its owned alias",
              run_k3s_alias(server, root, alias_name, "absent", []),
              ["alias/delItem", "alias/reconfigure"])
        check("cluster alias is absent after removal", server.aliases, {})

        server.aliases["foreign-alias"] = {
            "name": alias_name, "type": "host", "content": "198.51.100.21",
            "description": "Operator-managed cluster nodes"}
        check("same-name foreign alias is refused without writes",
              run_k3s_alias(server, root, alias_name, "present", alias_nodes,
                            expect_failure=True), [])
        check("foreign alias is untouched", server.aliases["foreign-alias"]["content"],
              "198.51.100.21")
        check("cluster Remove refuses a foreign alias before deleting it",
              run_k3s_alias(server, root, alias_name, "verify", [], expect_failure=True), [])
        check("foreign alias survives Remove preflight",
              server.aliases["foreign-alias"]["description"],
              "Operator-managed cluster nodes")
        server.aliases.clear()

        check("first deploy adds two egress rules and applies",
              run(server, root, wire, "slskd", declared),
              ["filter/addRule", "filter/addRule", "filter/apply"])
        owned = [r for r in server.rules["filter"].values()
                 if r["description"].startswith("homelab-infra: slskd egress ")]
        legacy = next(r for r in owned if r["description"].endswith("soulseek"))
        check("egress rule shape", owned and {k: owned[0][k] for k in (
            "interface", "source_net", "destination_net", "destination_port",
            "protocol", "action", "sequence")}, {
            "interface": "opt4", "source_net": "198.51.100.34", "destination_net": "any",
            "destination_port": "", "protocol": "TCP", "action": "pass", "sequence": "1000"})
        check("legacy egress has no source-port restriction",
              "source_port" not in legacy, True)
        peer = next(r for r in owned if r["description"].endswith("peer-range"))
        check("optional egress source port reaches the filter rule",
              peer.get("source_port"), "61000-65535")
        check("per-rule sequence overrides the global default",
              peer.get("sequence"), "750")
        check("disabled forward publishes nothing", server.rules["d_nat"], {})

        check("second deploy changes nothing",
              run(server, root, wire, "slskd", declared), [])

        owned[0]["destination_port"] = "2271"
        check("drift is corrected in place",
              run(server, root, wire, "slskd", declared), ["filter/setRule", "filter/apply"])

        peer["source_port"] = "61001-65535"
        check("source-port drift is corrected in place",
              run(server, root, wire, "slskd", declared), ["filter/setRule", "filter/apply"])

        next(r for r in server.rules["filter"].values()
             if r["description"].endswith("peer-range"))["sequence"] = "751"
        check("per-rule sequence drift is corrected in place",
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
              run(server, root, unwire, "slskd", {}),
              ["filter/delRule", "filter/delRule", "filter/apply"])
        check("foreign and sibling-instance rules survive",
              sorted(server.rules["filter"]), ["foreign", "sibling"])
        check("second remove changes nothing", run(server, root, unwire, "slskd", {}), [])

        qbt_defaults = yaml.safe_load(
            (repo / "ansible/vars/app-defaults/qbittorrent.yml").read_text()
        )["qbittorrent_defaults"]
        qbt_role_defaults = yaml.safe_load(
            (repo / "ansible/roles/qbittorrent/defaults/main.yml").read_text()
        )
        server.rules["filter"]["qbt-old"] = {
            "description": "homelab-infra: qbittorrent egress bittorrent-peers",
            "interface": "opt4", "source_net": "198.51.100.34"}
        qbt_app = {"torrenting_port": qbt_defaults["app"]["torrenting_port"]}
        check("qBittorrent replaces its broad legacy peer rule",
              run(server, root, wire, "qbittorrent", qbt_defaults["firewall"],
                  {"app_config": {"app": qbt_app}}),
              ["filter/addRule", "filter/addRule", "filter/addRule", "filter/addRule",
               "filter/delRule", "filter/apply"])
        qbt_rules = {r["description"].rsplit(" ", 1)[-1]: r
                     for r in server.rules["filter"].values()
                     if r.get("description", "").startswith("homelab-infra: qbittorrent egress ")}
        check("qBittorrent declares TCP peer source range",
              qbt_rules.get("bittorrent-peers-tcp", {}).get("source_port"),
              f"{qbt_role_defaults['qbittorrent_peer_outgoing_ports_min']}-{qbt_role_defaults['qbittorrent_peer_outgoing_ports_max']}")
        check("qBittorrent UDP peers use the listen source port",
              qbt_rules.get("bittorrent-peers-udp", {}).get("source_port"),
              str(qbt_defaults["app"]["torrenting_port"]))
        check("qBittorrent allows HTTP trackers and web fetches on TCP/80",
              (qbt_rules.get("bittorrent-web-http", {}).get("protocol"),
               qbt_rules.get("bittorrent-web-http", {}).get("destination_port")),
              ("TCP", "80"))
        check("qBittorrent allows HTTPS trackers and web fetches on TCP/443",
              (qbt_rules.get("bittorrent-web-https", {}).get("protocol"),
               qbt_rules.get("bittorrent-web-https", {}).get("destination_port")),
              ("TCP", "443"))
        check("qBittorrent rerun changes nothing",
              run(server, root, wire, "qbittorrent", qbt_defaults["firewall"],
                  {"app_config": {"app": qbt_app}}), [])
    server.shutdown()

    for failure in failures:
        print("FAIL:", failure)
    print("firewall wiring: %s" % ("FAILED" if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
