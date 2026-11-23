#!/usr/bin/env python3
"""Exercise the firewall account through Ansible against the shared API/Vaultwarden fake."""
import importlib.util
from pathlib import Path
import tempfile

import yaml

spec = importlib.util.spec_from_file_location(
    "account_fixture", Path(__file__).with_name("test-opnsense-dns-account.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
fixture.service = "firewall"
fixture.account_name = "homelab-infra-firewall"
fixture.account_privilege = ",".join(yaml.safe_load(
    (fixture.repo / "ansible/vars/opnsense-firewall-account.yml").read_text()
)["opnsense_firewall_account_privileges"])
fixture.allowed_endpoint, fixture.denied_endpoint = fixture.denied_endpoint, fixture.allowed_endpoint


def main():
    server, thread = fixture.initial_opnsense(legacy=True)
    original_legacy = dict(server.users["homelab-green-dns"])
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def run(name, fields, **kwargs):
                directory = root / name
                directory.mkdir()
                result, state = fixture.run_account(server, directory, fields, **kwargs)
                assert all(secret not in result.stdout + result.stderr for secret in
                           (fixture.admin_key, fixture.admin_secret, *fields.values()))
                stored = {field["name"]: field["value"] for field in
                          fixture.read_state(state)["items"][0]["fields"]}
                assert all(secret not in result.stdout + result.stderr
                           for secret in stored.values())
                return result, stored

            result, fields = run("create", {"api_key": fixture.old_key,
                                            "api_secret": fixture.old_secret})
            assert result.returncode == 0, result.stdout[-4000:] + result.stderr
            assert server.users[fixture.account_name]["priv"] == fixture.account_privilege
            assert server.users["homelab-green-dns"] == original_legacy
            mutations = len(server.mutations)
            result, repeated = run("confirm", fields)
            assert result.returncode == 0 and "changed=0" in result.stdout
            assert repeated == fields and len(server.mutations) == mutations
            for name, privileges in (
                ("missing", "page-filter-api,page-firewall-nat-portforward-edit"),
                ("extra", fixture.account_privilege + ",page-services-unbound"),
                ("reordered", ",".join(reversed(fixture.account_privilege.split(","))))):
                server.users[fixture.account_name]["priv"] = privileges
                result, stored = run(name, fields)
                assert result.returncode == 0, result.stdout[-4000:] + result.stderr
                assert stored == fields
                assert set(server.users[fixture.account_name]["priv"].split(",")) == set(
                    fixture.account_privilege.split(","))
                if name == "reordered":
                    assert "changed=0" in result.stdout
            mutations = len(server.mutations)
            result, _ = run("mismatch", {"api_key": "fixture-mismatch",
                                         "api_secret": "fixture-mismatch-secret"})
            assert result.returncode != 0 and len(server.mutations) == mutations
            result, rotated = run("rotate", fields, rotate=True)
            assert result.returncode == 0, result.stdout[-4000:] + result.stderr
            assert rotated != fields
            assert len([key for key in server.api_keys
                        if key["username"] == fixture.account_name]) == 1
            server.users[fixture.account_name]["descr"] = "Operator-owned"
            mutations = len(server.mutations)
            result, _ = run("foreign", rotated)
            assert result.returncode != 0 and len(server.mutations) == mutations
            assert server.users["homelab-green-dns"] == original_legacy
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    print("Firewall account create, confirm, privilege drift, mismatch, rotation and foreign refusal passed")


if __name__ == "__main__":
    main()
