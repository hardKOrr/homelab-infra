"""Confirmed firewall identity for rule/alias API fakes; mutations live in the lifecycle suite."""
import base64
import json
from pathlib import Path

import yaml

ADMIN_CREDENTIAL = {"api_key": "fixture-admin-key", "api_secret": "fixture-admin-secret"}


class ConfirmedFirewallAccount:
    def account_request(self, method):
        path = self.path.split("?", 1)[0]
        if path == "/api/unbound/settings/searchHostOverride":
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.reply(403, {})
            return True
        if not path.startswith("/api/auth/user/"):
            return False
        expected = "Basic " + base64.b64encode(b"fixture-admin-key:fixture-admin-secret").decode()
        if self.headers.get("Authorization") != expected:
            self.reply(401, {})
            return True
        if method == "POST":
            json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if path.endswith("/search"):
            privileges = yaml.safe_load((Path(__file__).resolve().parents[1]
                                        / "ansible/vars/opnsense-firewall-account.yml").read_text())
            rows = [{"name": "homelab-infra-firewall", "descr": "Owned by homelab-infra",
                     "uuid": "fixture-firewall-user", "priv": ",".join(
                         privileges["opnsense_firewall_account_privileges"]),
                     "scope": "user", "disabled": "0"}]
        elif path.endswith("/search_api_key"):
            rows = [{"username": "homelab-infra-firewall", "key": "fixture-key"}]
        else:
            self.reply(404, {})
            return True
        self.reply(200, {"rows": rows, "total": len(rows)})
        return True
