#!/usr/bin/env python3
"""Rebuild deployed routes through the production Caddy wiring task and a fake API."""

import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

import yaml

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_PLAYBOOK = Path(
    os.environ.get(
        "GATE_ANSIBLE_PLAYBOOK",
        Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook",
    )
)
failures = []


def check(name, actual, expected):
    if actual != expected:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")


class FakeCaddy(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.calls.append(f"GET {self.path}")
        if self.path == "/config/apps/http/servers/srv0/routes":
            return self.reply(200, self.server.routes)
        if self.path.startswith("/id/"):
            route_id = self.path.removeprefix("/id/")
            matches = [route for route in self.server.routes if route.get("@id") == route_id]
            return self.reply(200, matches[0]) if matches else self.reply(404, {})
        return self.reply(404, {})

    def do_POST(self):
        self.server.calls.append(f"POST {self.path}")
        if self.path != "/config/apps/http/servers/srv0/routes":
            return self.reply(404, {})
        route = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.routes.append(route)
        return self.reply(200, {})

    def do_PATCH(self):
        self.server.calls.append(f"PATCH {self.path}")
        if not self.path.startswith("/id/"):
            return self.reply(404, {})
        route_id = self.path.removeprefix("/id/")
        route = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        for index, existing in enumerate(self.server.routes):
            if existing.get("@id") == route_id:
                self.server.routes[index] = route
                return self.reply(200, {})
        return self.reply(404, {})

    def reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *_args):
        pass


def run(playbook, inventory, environment, extra_vars_file=None, expect_failure=False):
    command = [str(ANSIBLE_PLAYBOOK), "-i", str(inventory), str(playbook)]
    if extra_vars_file is not None:
        command.extend(["--extra-vars", f"@{extra_vars_file}"])
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    if expect_failure and result.returncode == 0:
        failures.append("route rebuild unexpectedly succeeded with invalid app records")
    elif not expect_failure and result.returncode != 0:
        failures.append(
            "route rebuild play failed:\n"
            + result.stdout[-2500:]
            + result.stderr[-2500:]
        )
    return result



def route_upstream_dials(value):
    if isinstance(value, dict):
        own = [upstream.get("dial") for upstream in value.get("upstreams", [])]
        nested = [dial for child in value.values() for dial in route_upstream_dials(child)]
        return own + nested
    if isinstance(value, list):
        return [dial for child in value for dial in route_upstream_dials(child)]
    return []


def inventory_fixture():
    def guest(tags, address, vmid, vmtype="lxc"):
        return {
            "ansible_host": address,
            "proxmox_node": "fixture-node",
            "proxmox_vmid": vmid,
            "proxmox_vmtype": vmtype,
            "proxmox_tags_parsed": ["_+lab", *tags],
        }

    return {
        "all": {
            "vars": {"homelabinfra_proxmox_inventory_complete": True},
            "hosts": {"localhost": {"ansible_connection": "local"}},
            "children": {
                "lab_managed": {
                    "hosts": {
                        "radarr-host": guest(["_radarr-4k"], "192.0.2.25", 101),
                        "authentik-host": guest(["_authentik"], "192.0.2.26", 102),
                        "caddy-host": guest(["_edge-router"], "192.0.2.27", 103),
                        "k8s-one": guest(["_homepage"], "192.0.2.31", 201),
                        "k8s-two": guest(["_homepage"], "192.0.2.32", 202),
                        "wireguard-host": guest(["_wireguard"], "192.0.2.41", 301),
                        "pdm-host": guest(
                            ["_proxmox-datacenter-manager"], "192.0.2.51", 401, "qemu"
                        ),
                        "k3s-host": guest(["_k3s-cluster"], "192.0.2.61", 501, "qemu"),
                    }
                },
                "lab_app_authentik": {"hosts": {"authentik-host": {}}},
                "lab_app_radarr_4k": {"hosts": {"radarr-host": {}}},
            },
        }
    }


def main():
    if not ANSIBLE_PLAYBOOK.is_file():
        print(f"ansible-playbook is required at {ANSIBLE_PLAYBOOK}", file=sys.stderr)
        return 2

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeCaddy)
    server.routes = []
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        config_apps = work / "config/apps"
        config_apps.mkdir(parents=True)

        (config_apps / "radarr-4k.yml").write_text(
            yaml.safe_dump(
                {
                    "app": {"port": 17878},
                    "routing": {
                        "identity": "forward_auth",
                        "subdomain": "radarr-four-k",
                    },
                }
            ),
            encoding="utf-8",
        )
        (config_apps / "homepage.yml").write_text(
            yaml.safe_dump(
                {
                    "routing": {"estate": "alpha", "subdomain": "home"},
                }
            ),
            encoding="utf-8",
        )
        (work / "config/infrastructure.yml").write_text(
            yaml.safe_dump(
                {
                    "domain": "example.test",
                    "domains": {
                        "main": {"default": True, "domain": "example.test"},
                        "alpha": {"domain": "alpha.example.test"},
                    },
                }
            ),
            encoding="utf-8",
        )

        inventory = work / "inventory.yml"
        inventory.write_text(yaml.safe_dump(inventory_fixture()), encoding="utf-8")
        extra_vars_file = work / "vars.yml"
        extra_vars_file.write_text(
            yaml.safe_dump(
                {
                    "instance": "edge-router",
                    "caddy_test_admin_port": server.server_port,
                    "caddy_rebuild_config_root": str(work / "config"),
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        playbook = ROOT / "gate/fixtures/caddy-route-rebuild.yml"

        environment = os.environ.copy()
        environment["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
        environment["ANSIBLE_NOCOLOR"] = "1"

        run(playbook, inventory, environment, extra_vars_file)
        routes = {route.get("@id"): route for route in server.routes}
        authentik_route = routes.get("route_authentik", {})
        homepage_route = routes.get("route_homepage", {})
        radarr_route = routes.get("route_radarr-4k", {})
        check("fresh Caddy gets one route for each deployed HTTP app", sorted(routes), [
            "route_authentik", "route_homepage", "route_radarr-4k"
        ])
        check("identity-none Authentik still gets a reverse-proxy route",
              (authentik_route.get("handle") or [{}])[0].get("handler"), "reverse_proxy")
        check("Authentik route uses its tagged stack host",
              route_upstream_dials(authentik_route), ["192.0.2.26:9000"])

        hostnames = {}
        for route_id, route in routes.items():
            hostnames[route_id] = route["match"][0]["host"][0]
        check("instance config controls the native upstream",
              route_upstream_dials(radarr_route),
              ["auth.example.test", "auth.example.test", "192.0.2.25:17878"])
        check("Kubernetes apps route through the ingress VIP",
              route_upstream_dials(homepage_route), ["192.0.2.80:80"])
        check("default estate domain and subdomain are preserved",
              hostnames.get("route_radarr-4k"), "radarr-four-k.example.test")
        check("Authentik default hostname is preserved",
              hostnames.get("route_authentik"), "auth.example.test")
        check("instance estate domain and subdomain are preserved",
              hostnames.get("route_homepage"), "home.alpha.example.test")
        check("estate without SSO stays a plain reverse proxy",
              (homepage_route.get("handle") or [{}])[0].get("handler"), "reverse_proxy")
        radarr_handler = (radarr_route.get("handle") or [{}])[0]
        check("default-estate forward-auth route has an auth subroute",
              radarr_handler.get("handler"), "subroute")
        authentik_dial = ""
        if radarr_handler.get("routes"):
            auth_routes = radarr_handler["routes"]
            if len(auth_routes) > 1 and auth_routes[1].get("handle"):
                authentik_dial = (
                    auth_routes[1]["handle"][0].get("upstreams", [{}])[0].get("dial", "")
                )
        check("default-estate forward-auth route uses its Authentik", authentik_dial,
              "auth.example.test")
        check("non-routed and appliance apps stay out of the route table", len(server.routes), 3)

        server.calls.clear()
        run(playbook, inventory, environment, extra_vars_file)
        check("repeat Caddy deploy keeps each route single", len(server.routes), 3)
        check("repeat Caddy deploy posts or patches no routes",
              [call for call in server.calls if call.startswith(("POST ", "PATCH "))], [])

        broken_inventory_data = inventory_fixture()
        managed_hosts = broken_inventory_data["all"]["children"]["lab_managed"]["hosts"]
        managed_hosts["stale-tag-host"] = {
            **managed_hosts["authentik-host"],
            "proxmox_tags_parsed": ["_+lab", "_no-default-app"],
        }
        managed_hosts["orphan-app-host"] = {
            **managed_hosts["authentik-host"],
            "ansible_host": "",
            "proxmox_tags_parsed": ["_+lab", "_radarr-orphan"],
        }
        broken_inventory = work / "broken-inventory.yml"
        broken_inventory.write_text(
            yaml.safe_dump(broken_inventory_data), encoding="utf-8"
        )
        server.routes.clear()
        server.calls.clear()
        broken_result = run(
            playbook,
            broken_inventory,
            environment,
            extra_vars_file,
            expect_failure=True,
        )
        failure_output = broken_result.stdout + broken_result.stderr
        summary_start = failure_output.rfind(
            "assertion: _caddy_rebuild_errors | length == 0"
        )
        summary_end = failure_output.find("PLAY RECAP", summary_start)
        summary = failure_output[summary_start:summary_end]
        check("all invalid app records appear in one final failure",
              "Caddy route rebuild failures (2):" in summary, True)
        check("unmatched tag is listed in the final failure", "no-default-app" in summary, True)
        check("unresolved hosting guest is listed in the final failure",
              "radarr-orphan" in summary, True)
        check("valid routes are rebuilt despite invalid app records",
              sorted(route.get("@id") for route in server.routes),
              ["route_authentik", "route_homepage", "route_radarr-4k"])
        check("each valid route remains single after the failed rebuild",
              len(server.routes), 3)

        server.calls.clear()
        app_deploy_wiring = work / "app-deploy-wiring.yml"
        app_deploy_wiring.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "gather_facts": False,
                        "vars": {
                            "homelabinfra_infra": {
                                "domain": "example.test",
                                "reverse_proxy": {
                                    "provider": "caddy",
                                    "host": "http://127.0.0.1",
                                    "port": server.server_port,
                                    "internal_cidrs": ["192.0.2.0/24"],
                                },
                                "sso": {
                                    "provider": "authentik",
                                    "host": "https://auth.example.test",
                                },
                            },
                            "wiring_app_name": "radarr-4k",
                            "wiring_upstream_host": "192.0.2.25",
                            "wiring_upstream_port": 17878,
                            "wiring_domain": "radarr-four-k.example.test",
                            "wiring_identity_mode": "forward_auth",
                            "wiring_access": "internal",
                        },
                        "tasks": [
                            {
                                "ansible.builtin.include_tasks": str(
                                    ROOT / "ansible/tasks/wiring/caddy.yml"
                                )
                            }
                        ],
                    }
                ],
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        run(app_deploy_wiring, inventory, environment)
        check("app Deploy leaves exactly one matching route",
              sum(route.get("@id") == "route_radarr-4k" for route in server.routes), 1)
        check("app Deploy after Caddy convergence makes no route writes",
              [call for call in server.calls if call.startswith(("POST ", "PATCH "))], [])

    server.shutdown()
    server.server_close()
    for failure in failures:
        print("FAIL:", failure)
    print("Caddy route rebuild: %s" % ("FAILED" if failures else "ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
