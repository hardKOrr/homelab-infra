#!/usr/bin/env python3
"""Exercise DNS-name renames against fake OPNsense, Caddy, and Pi-hole APIs.

Checks: DNS-name renames remove only the instance-owned OPNsense override or the former
Caddy route hostname in Pi-hole, while preserving foreign and sibling records.
"""

import base64
import http.server
import json
import re
import subprocess
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
failures = []


def check(name, actual, expected):
    if actual != expected:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")


class FakeDnsApis(http.server.BaseHTTPRequestHandler):
    def reply(self, status, payload=None):
        self.send_response(status)
        if payload is not None:
            body = json.dumps(payload).encode()
            self.send_header("Content-Type", "application/json")
        else:
            body = b""
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

    def caddy_route(self, route_id):
        return next((route for route in self.server.caddy_routes
                     if route.get("@id") == route_id), None)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/auth/user/search_api_key":
            if self.headers.get("Authorization") != "Basic " + base64.b64encode(
                b"fixture-admin-key:fixture-admin-secret"
            ).decode():
                return self.reply(401, {})
            return self.reply(200, {"rows": [{
                "username": "homelab-infra-dns",
                "key": "fixture-key",
                "id": base64.b64encode(b"fixture-key").decode(),
            }], "total": 1})
        if path == "/config/apps/http/servers/srv0/routes":
            return self.reply(200, self.server.caddy_routes)
        if path.startswith("/id/"):
            route = self.caddy_route(path.rsplit("/", 1)[-1])
            return self.reply(200 if route else 404, route or {})
        if path == "/api/config/dns/hosts":
            if self.headers.get("X-FTL-SID") != "fixture-sid":
                return self.reply(401, {})
            return self.reply(200, {"config": {"dns": {"hosts": self.server.pihole_hosts}}})
        return self.reply(404, {})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/auth/user/search":
            if self.headers.get("Authorization") != "Basic " + base64.b64encode(
                b"fixture-admin-key:fixture-admin-secret"
            ).decode():
                return self.reply(401, {})
            return self.reply(200, {"rows": [dict(self.server.opnsense_dns_user)], "total": 1})
        if path == "/api/firewall/filter/searchRule":
            return self.reply(403, {})
        if path == "/api/unbound/settings/searchHostOverride":
            if self.headers.get("Authorization") != "Basic " + base64.b64encode(
                b"fixture-key:fixture-secret"
            ).decode():
                return self.reply(401, {})
            phrase = self.read_json().get("searchPhrase", "").lower()
            rows = [
                {"uuid": uid, **record}
                for uid, record in self.server.opnsense_overrides.items()
                if phrase in " ".join(str(value) for value in [uid, *record.values()]).lower()
            ]
            return self.reply(200, {"rows": rows, "total": len(rows)})
        if path == "/api/unbound/settings/addHostOverride":
            host = self.read_json()["host"]
            uid = f"added-{len(self.server.opnsense_overrides)}"
            self.server.opnsense_overrides[uid] = host
            return self.reply(200, {"result": "saved"})
        if path.startswith("/api/unbound/settings/setHostOverride/"):
            uid = path.rsplit("/", 1)[-1]
            self.server.opnsense_overrides[uid] = self.read_json()["host"]
            return self.reply(200, {"result": "saved"})
        if path.startswith("/api/unbound/settings/delHostOverride/"):
            uid = path.rsplit("/", 1)[-1]
            self.server.opnsense_overrides.pop(uid, None)
            return self.reply(200, {"result": "deleted"})
        if path == "/api/unbound/service/reconfigure":
            return self.reply(200, {"status": "OK"})
        if path == "/api/auth":
            if self.read_json().get("password") != "fixture-password":
                return self.reply(401, {})
            return self.reply(200, {"session": {"sid": "fixture-sid"}})
        if path == "/config/apps/http/servers/srv0/routes":
            self.server.caddy_routes.append(self.read_json())
            return self.reply(200)
        return self.reply(404, {})

    def do_PATCH(self):
        path = urlsplit(self.path).path
        if path.startswith("/id/"):
            route_id = path.rsplit("/", 1)[-1]
            route = self.caddy_route(route_id)
            if route is None:
                return self.reply(404, {})
            route.clear()
            route.update(self.read_json())
            return self.reply(200)
        return self.reply(404, {})

    def do_PUT(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/config/dns/hosts/"):
            if self.headers.get("X-FTL-SID") != "fixture-sid":
                return self.reply(401, {})
            entry = unquote(path.rsplit("/", 1)[-1])
            if entry not in self.server.pihole_hosts:
                self.server.pihole_hosts.append(entry)
            return self.reply(200, {})
        return self.reply(404, {})

    def do_DELETE(self):
        path = urlsplit(self.path).path
        if path == "/api/auth":
            return self.reply(204)
        if path.startswith("/api/config/dns/hosts/"):
            if self.headers.get("X-FTL-SID") != "fixture-sid":
                return self.reply(401, {})
            entry = unquote(path.rsplit("/", 1)[-1])
            self.server.pihole_hosts = [row for row in self.server.pihole_hosts if row != entry]
            return self.reply(200, {})
        return self.reply(404, {})

    def log_message(self, *_args):
        pass


def run_play(root, name, vars_, tasks):
    play = [{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": vars_,
        "tasks": tasks,
    }]
    path = root / f"{name}.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        failures.append(f"{name} Ansible run failed:\n{result.stdout[-2500:]}\n{result.stderr[-1000:]}")
    return result.stdout


def changed_count(output):
    match = re.search(r"localhost\s*:\s*ok=\d+\s+changed=(\d+)", output)
    return int(match.group(1)) if match else None


def main():
    owned_description = "homelab-infra: ntfy"
    manual = {
        "hostname": "drill-manual",
        "domain": "example.test",
        "server": "192.0.2.10",
        "description": "manual: drill-474b",
        "enabled": "1",
    }
    sibling = {
        "hostname": "other-app",
        "domain": "example.test",
        "server": "192.0.2.10",
        "description": "homelab-infra: other-app",
        "enabled": "1",
    }
    old_override = {
        "hostname": "notify-old",
        "domain": "example.test",
        "server": "192.0.2.10",
        "description": owned_description,
        "enabled": "1",
    }
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeDnsApis)
    server.opnsense_dns_user = {
        "uuid": "fixture-dns-user",
        "name": "homelab-infra-dns",
        "descr": "Owned by homelab-infra",
        "scope": "user",
        "disabled": "0",
        "expires": "",
        "shell": "",
        "authorizedkeys": "",
        "priv": "page-services-unbound",
        "group_memberships": "",
    }
    server.opnsense_overrides = {
        "old-owned": deepcopy(old_override),
        "manual": deepcopy(manual),
        "sibling": deepcopy(sibling),
    }
    server.pihole_hosts = [
        "192.0.2.10 notify-old.example.test",
        "192.0.2.10 drill-manual.example.test",
        "192.0.2.10 other-app.example.test",
    ]
    server.caddy_routes = [{
        "@id": "route_ntfy",
        "match": [{"host": ["notify-old.example.test"]}],
        "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "192.0.2.20:80"}]}],
        "terminal": True,
    }]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host = f"http://127.0.0.1:{server.server_port}"
    opn_vars = {
        "homelabinfra_infra": {"dns": {
            "provider": "opnsense", "host": host,
            "api_key": "fixture-key", "api_secret": "fixture-secret",
            "validate_certs": False,
        }, "firewall-admin": {
            "api_key": "fixture-admin-key", "api_secret": "fixture-admin-secret",
        }},
        "wiring_app_name": "ntfy",
        "wiring_domain": "alerts.example.test",
        "wiring_ip": "192.0.2.10",
    }
    pihole_vars = {
        "homelabinfra_infra": {
            "dns": {
                "provider": "pihole", "host": host, "api_key": "fixture-password",
                "validate_certs": False,
            },
            "reverse_proxy": {
                "provider": "caddy", "host": "http://127.0.0.1",
                "port": server.server_port, "internal_cidrs": ["192.0.2.0/24"],
            },
        },
        "wiring_app_name": "ntfy",
        "wiring_upstream_host": "192.0.2.20",
        "wiring_upstream_port": 80,
        "wiring_domain": "alerts.example.test",
        "wiring_ip": "192.0.2.10",
        "wiring_access": "public",
        "wiring_identity_mode": "catalog",
    }

    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            opn_task = [{"ansible.builtin.include_tasks": str(repo / "ansible/tasks/wiring/opnsense.yml")}]
            first = run_play(root, "opnsense-rename", opn_vars, opn_task)
            rows = server.opnsense_overrides
            owned = [record for record in rows.values()
                     if record.get("description") == owned_description]
            check("OPNsense leaves one owned record at the new name",
                  [(row.get("hostname"), row.get("domain")) for row in owned],
                  [("alerts", "example.test")])
            check("OPNsense removes the old owned record",
                  "old-owned" in rows, False)
            check("OPNsense leaves the foreign manual record unchanged",
                  rows.get("manual"), manual)
            check("OPNsense leaves a sibling application's record unchanged",
                  rows.get("sibling"), sibling)

            repeated = run_play(root, "opnsense-repeat", opn_vars, opn_task)
            check("OPNsense repeat Deploy reports no changes", changed_count(repeated), 0)

            pihole_tasks = [
                {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/wiring/caddy.yml")},
                {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/wiring/pihole.yml")},
            ]
            pihole_first = run_play(root, "pihole-rename", pihole_vars, pihole_tasks)
            check("Pi-hole removes the old Caddy route hostname",
                  "192.0.2.10 notify-old.example.test" in server.pihole_hosts, False)
            check("Pi-hole has one record at the new name",
                  server.pihole_hosts.count("192.0.2.10 alerts.example.test"), 1)
            check("Pi-hole leaves the foreign manual record unchanged",
                  "192.0.2.10 drill-manual.example.test" in server.pihole_hosts, True)
            check("Pi-hole leaves a sibling application's record unchanged",
                  "192.0.2.10 other-app.example.test" in server.pihole_hosts, True)

            pihole_repeat = run_play(root, "pihole-repeat", pihole_vars, pihole_tasks)
            check("Pi-hole repeat Deploy reports no changes", changed_count(pihole_repeat), 0)
            check("Pi-hole first Deploy changes the old route and new DNS record",
                  changed_count(pihole_first) is not None and changed_count(pihole_first) > 0, True)
            check("OPNsense first Deploy changed state",
                  changed_count(first) is not None and changed_count(first) > 0, True)
    finally:
        server.shutdown()
        server.server_close()

    if failures:
        raise SystemExit("\n".join(failures))
    print("DNS rename wiring checks passed")


if __name__ == "__main__":
    main()
