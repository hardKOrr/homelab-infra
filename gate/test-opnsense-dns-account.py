#!/usr/bin/env python3
"""Exercise the owned OPNsense DNS-user lifecycle against a local API and Vaultwarden fake."""

import base64
import http.server
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
python = Path.home() / ".venvs/homelab-ansible/bin/python"
admin_key = "fixture-admin-key"
admin_secret = "fixture-admin-secret"
old_key = "fixture-old-dns-key"
old_secret = "fixture-old-dns-secret"
existing_owned_key = "AA>BB?CC"
account_name = "homelab-infra-dns"
account_description = "Owned by homelab-infra"
account_privilege = "page-services-unbound"
failures = []


def check(name, condition):
    if not condition:
        failures.append(name)


def account_user(*, description=account_description, privilege=account_privilege):
    return {
        "uuid": "dns-user-id",
        "name": account_name,
        "descr": description,
        "scope": "user",
        "disabled": "0",
        "expires": "",
        "shell": "",
        "authorizedkeys": "",
        "priv": privilege,
        "group_memberships": "",
    }


def legacy_user():
    return {
        "uuid": "legacy-dns-id",
        "name": "homelab-green-dns",
        "descr": "Owned by homelab green-lab DNS automation",
        "scope": "user",
        "disabled": "0",
        "expires": "",
        "shell": "",
        "authorizedkeys": "",
        "priv": account_privilege,
        "group_memberships": "",
    }


class FakeOPNsense(http.server.BaseHTTPRequestHandler):
    def _auth_user(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return ""
        try:
            decoded = base64.b64decode(header[6:]).decode()
            key, secret = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return ""
        if key == admin_key and secret == admin_secret:
            return "admin"
        for item in self.server.api_keys:
            if item["key"] == key and item["secret"] == secret:
                return item["username"]
        return ""

    def _reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def _record(self, username, status):
        self.server.calls.append((self.command, urlparse(self.path).path, username, status))

    def do_POST(self):
        path = urlparse(self.path).path.strip("/").split("/")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        username = self._auth_user()
        if len(path) >= 3 and path[:2] == ["api", "auth"]:
            if username != "admin":
                self._record(username, 403 if username else 401)
                return self._reply(403 if username else 401, {})
            if path[2:] == ["user", "search"]:
                phrase = body.get("searchPhrase", "")
                rows = [dict(user) for user in self.server.users.values()
                        if phrase in user["name"]]
                self._record(username, 200)
                return self._reply(200, {"rows": rows, "total": len(rows)})
            if path[2:] == ["user", "add"]:
                user = body.get("user", {})
                self.server.users[user["name"]] = {**user, "uuid": "dns-user-id"}
                self.server.mutations.append("user/add")
                self._record(username, 200)
                return self._reply(200, {"result": "saved", "uuid": "dns-user-id"})
            if len(path) == 5 and path[2:4] == ["auth", "user"]:
                # Kept as a defensive route guard; normal user endpoints are handled below.
                self._record(username, 404)
                return self._reply(404, {})
            if len(path) == 5 and path[2:4] == ["user", "set"]:
                target = next((user for user in self.server.users.values()
                               if user.get("uuid") == path[4]), None)
                if target is None:
                    self._record(username, 404)
                    return self._reply(404, {})
                target.update(body.get("user", {}))
                self.server.mutations.append("user/set")
                self._record(username, 200)
                return self._reply(200, {"result": "saved"})
            if len(path) == 5 and path[2:4] == ["user", "del_api_key"]:
                key = base64.b64decode(unquote(path[4])).decode()
                index = next((index for index, item in enumerate(self.server.api_keys)
                              if item["key"] == key), None)
                if index is None:
                    self._record(username, 404)
                    return self._reply(404, {"result": "not found"})
                self.server.api_keys.pop(index)
                self.server.mutations.append("user/del_api_key")
                self._record(username, 200)
                return self._reply(200, {"result": "deleted"})
            if len(path) == 5 and path[2:4] == ["user", "add_api_key"]:
                target = self.server.users.get(path[4])
                if target is None:
                    self._record(username, 404)
                    return self._reply(404, {"result": "failed"})
                index = len([item for item in self.server.api_keys
                             if item["username"] == path[4]]) + 1
                issued = {"username": path[4], "key": f"fixture-new-key-{index}",
                          "secret": f"fixture-new-secret-{index}"}
                self.server.api_keys.append(issued)
                self.server.mutations.append("user/add_api_key")
                self._record(username, 200)
                return self._reply(200, {"result": "ok", "key": issued["key"],
                                         "secret": issued["secret"]})

        if path == ["api", "unbound", "settings", "searchHostOverride"]:
            allowed = username and self.server.users.get(username, {}).get("priv") == account_privilege
            status = 200 if allowed else (403 if username else 401)
            self._record(username, status)
            return self._reply(status, {"rows": [], "total": 0} if status == 200 else {})

        if path == ["api", "firewall", "filter", "searchRule"]:
            status = 403 if username and username != "admin" else 401
            self._record(username, status)
            return self._reply(status, {})

        self._record(username, 404)
        return self._reply(404, {})

    def do_GET(self):
        path = urlparse(self.path).path.strip("/").split("/")
        username = self._auth_user()
        if path == ["api", "auth", "user", "search_api_key"]:
            if username != "admin":
                status = 403 if username else 401
                self._record(username, status)
                return self._reply(status, {})
            self._record(username, 200)
            return self._reply(200, {"rows": self.server.api_keys,
                                     "total": len(self.server.api_keys)})
        self._record(username, 404)
        return self._reply(404, {})

    def log_message(self, *_args):
        pass


def make_bw_state(item_fields):
    return {
        "items": [{
            "id": "dns-item-id",
            "name": "homelab-infra/dns",
            "type": 2,
            "organizationId": "org-id",
            "collectionIds": ["collection-id"],
            "secureNote": {"type": 0},
            "fields": [{"name": key, "value": value, "type": 1}
                       for key, value in item_fields.items()],
        }],
        "invocations": [],
    }


def make_fake_bw(directory):
    state_path = directory / "bw-state.json"
    binary_dir = directory / "bin"
    binary_dir.mkdir()
    script = binary_dir / "bw"
    script.write_text("""#!/usr/bin/env python3
import base64, json, os, sys
from pathlib import Path

args = sys.argv[1:]
path = Path(os.environ['FAKE_BW_STATE'])
state = json.loads(path.read_text())
state.setdefault('invocations', []).append(args[:2])

def save():
    path.write_text(json.dumps(state))

if args == ['status']:
    print(json.dumps({'status': 'unlocked', 'userEmail': 'automation@example.com', 'userId': 'automation'}))
elif args[:2] == ['list', 'organizations']:
    print(json.dumps([{'id': 'org-id', 'name': 'homelab-infra'}]))
elif args[:2] == ['list', 'org-collections']:
    print(json.dumps([{'id': 'collection-id', 'name': 'platform-secrets'}]))
elif args[:2] == ['list', 'org-members']:
    print(json.dumps([{'id': 'automation', 'email': 'automation@example.com', 'type': 1, 'accessAll': True}]))
elif args[:2] == ['list', 'items']:
    print(json.dumps(state['items']))
elif args[:2] == ['get', 'item']:
    item = next((value for value in state['items'] if value['id'] == args[2]), None)
    if item is None:
        sys.exit(1)
    print(json.dumps(item))
elif args == ['encode']:
    print(base64.b64encode(sys.stdin.buffer.read()).decode())
elif args[:2] == ['edit', 'item']:
    value = json.loads(base64.b64decode(args[3]))
    value['id'] = args[2]
    state['items'] = [value if item['id'] == args[2] else item for item in state['items']]
    save()
    print(json.dumps(value))
elif args[:2] == ['create', 'item']:
    value = json.loads(base64.b64decode(args[2]))
    value['id'] = 'dns-item-id'
    state['items'].append(value)
    save()
    print(json.dumps(value))
elif args == ['sync']:
    print('{}')
else:
    print('unsupported fake bw command', file=sys.stderr)
    sys.exit(2)
""")
    script.chmod(0o755)
    return state_path, binary_dir


def read_state(path):
    return json.loads(path.read_text())


def initial_opnsense(*, owned=False, foreign=False, legacy=False, existing_key=False):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOPNsense)
    server.users = {}
    server.api_keys = []
    server.calls = []
    server.mutations = []
    if legacy:
        server.users["homelab-green-dns"] = legacy_user()
        server.api_keys.append({"username": "homelab-green-dns", "key": old_key,
                                "secret": old_secret})
    if owned or foreign:
        user = account_user(description="Operator-owned" if foreign else account_description)
        server.users[account_name] = user
    if existing_key:
        server.api_keys.append({"username": account_name, "key": existing_owned_key,
                                "secret": "fixture-owned-secret"})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def run_account(server, directory, item_fields, *, rotate=False):
    state_path, binary_dir = make_fake_bw(directory)
    state_path.write_text(json.dumps(make_bw_state(item_fields)))
    wiring_dir = directory / "wiring"
    wiring_dir.mkdir()
    (wiring_dir / "opnsense.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.include_tasks": str(
            repo / "ansible/tasks/wiring/opnsense-dns-account.yml"),
    }]))
    play = [
        {
            "name": "Load runtime firewall facts",
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": [{
                "ansible.builtin.set_fact": {
                    "homelabinfra_infra": {
                        "dns": {
                            "provider": "opnsense",
                            "host": f"http://127.0.0.1:{server.server_port}",
                            "api_key": item_fields.get("api_key", ""),
                            "api_secret": item_fields.get("api_secret", ""),
                            "validate_certs": False,
                        },
                        "firewall-admin": {
                            "api_key": admin_key,
                            "api_secret": admin_secret,
                        },
                    },
                    "_opn_base": f"http://127.0.0.1:{server.server_port}",
                    "_opn_validate": False,
                    "opnsense_dns_rotate_key": rotate,
                },
                "no_log": True,
                "changed_when": False,
            }],
        },
        {
            "name": "Exercise OPNsense DNS account through the dynamic wiring path",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {"wiring_dir": str(wiring_dir)},
            "tasks": [{
                "ansible.builtin.include_tasks":
                    "{{ wiring_dir }}/{{ homelabinfra_infra.dns.provider }}.yml",
            }],
        },
    ]
    play_path = directory / "play.yml"
    play_path.write_text(yaml.safe_dump(play))
    env = os.environ.copy()
    env.update({
        "ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg"),
        "ANSIBLE_NOCOLOR": "1",
        "BW_SESSION": "fixture-session",
        "BITWARDENCLI_APPDATA_DIR": str(directory),
        "FAKE_BW_STATE": str(state_path),
        "PATH": f"{binary_dir}:{env.get('PATH', '')}",
    })
    result = subprocess.run(
        [str(ansible), "-i", "localhost,", "-c", "local", str(play_path)],
        capture_output=True, text=True, check=False, env=env,
    )
    return result, state_path


def status_for_dns_key(server, key, secret, endpoint):
    token = base64.b64encode(f"{key}:{secret}".encode()).decode()
    request = Request(endpoint, data=b'{"current":1,"rowCount":1}',
                      headers={"Authorization": f"Basic {token}",
                               "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status
    except HTTPError as error:
        return error.code


def main():
    checks = []
    servers = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)

        # Creation migrates from the prior DNS credential without touching its account.
        server, thread = initial_opnsense(legacy=True)
        servers.append((server, thread))
        create_dir = root / "create"
        create_dir.mkdir()
        created, bw_state_path = run_account(
            server, create_dir, {"api_key": old_key, "api_secret": old_secret,
                                 "keep_field": "preserve-me"})
        created_state = read_state(bw_state_path)
        item = created_state["items"][0]
        created_user = server.users.get(account_name, {})
        created_key = next((value for value in server.api_keys
                            if value["username"] == account_name), {})
        check("create run succeeds", created.returncode == 0)
        check("created account uses the requested name and description",
              created_user.get("name") == account_name
              and created_user.get("descr") == account_description)
        check("created account has only the Unbound privilege and no shell/group",
              created_user.get("priv") == account_privilege
              and not created_user.get("group_memberships")
              and not created_user.get("shell")
              and not created_user.get("authorizedkeys"))
        item_values = {field["name"]: field["value"] for field in item.get("fields", [])}
        check("new API pair is stored hidden and the other DNS item field is preserved",
              item_values.get("api_key") == created_key.get("key")
              and item_values.get("api_secret") == created_key.get("secret")
              and item_values.get("keep_field") == "preserve-me"
              and all(field.get("type") == 1 for field in item.get("fields", [])))
        check("old DNS account remains unchanged during cutover",
              server.users.get("homelab-green-dns", {}).get("descr")
              == legacy_user()["descr"])
        check("new DNS key can read Unbound and is refused by firewall rules API",
              status_for_dns_key(
                  server, created_key.get("key", ""), created_key.get("secret", ""),
                  f"http://127.0.0.1:{server.server_port}/api/unbound/settings/searchHostOverride",
              ) == 200
              and status_for_dns_key(
                  server, created_key.get("key", ""), created_key.get("secret", ""),
                  f"http://127.0.0.1:{server.server_port}/api/firewall/filter/searchRule",
              ) == 403)
        check("create run does not print fixture credentials",
              all(value not in created.stdout + created.stderr
                  for value in (admin_key, admin_secret, old_key, old_secret,
                                created_key.get("key", ""), created_key.get("secret", ""))))
        mutation_count = len(server.mutations)
        repeat_dir = root / "repeat"
        repeat_dir.mkdir()
        repeated, repeat_state_path = run_account(server, repeat_dir, item_values)
        check("repeat run keeps the same key and reports no changes",
              repeated.returncode == 0 and "changed=0" in repeated.stdout
              and len(server.mutations) == mutation_count
              and {field["name"]: field["value"] for field in
                   read_state(repeat_state_path)["items"][0].get("fields", [])}
              == item_values)
        check("repeat run does not print fixture credentials",
              all(value not in repeated.stdout + repeated.stderr
                  for value in (admin_key, admin_secret,
                                item_values.get("api_key", ""),
                                item_values.get("api_secret", ""))))
        checks.append("create")

        # An unchanged owned account validates the exact saved pair without a write.
        server, thread = initial_opnsense(owned=True, existing_key=True)
        servers.append((server, thread))
        confirm_dir = root / "confirm"
        confirm_dir.mkdir()
        confirmed, confirm_state_path = run_account(
            server, confirm_dir,
            {"api_key": existing_owned_key, "api_secret": "fixture-owned-secret"})
        check("confirm run succeeds", confirmed.returncode == 0)
        check("confirm run reports no changes", "changed=0" in confirmed.stdout)
        check("confirm run performs no account or API-key writes", server.mutations == [])
        check("confirm run does not call the Vaultwarden CLI",
              read_state(confirm_state_path).get("invocations", []) == [])
        check("confirm run does not print fixture credentials",
              all(value not in confirmed.stdout + confirmed.stderr
                  for value in (existing_owned_key, "fixture-owned-secret",
                                admin_key, admin_secret)))
        checks.append("confirm")

        # Same username with another description is foreign and remains untouched.
        server, thread = initial_opnsense(foreign=True)
        servers.append((server, thread))
        foreign_dir = root / "foreign"
        foreign_dir.mkdir()
        refused, _ = run_account(server, foreign_dir,
                                 {"api_key": old_key, "api_secret": old_secret})
        check("foreign account is refused with ownership guidance",
              refused.returncode != 0
              and "Refusing to adopt OPNsense user homelab-infra-dns" in refused.stdout)
        check("foreign account is not modified", server.mutations == [])
        check("foreign refusal does not print credentials",
              all(value not in refused.stdout + refused.stderr
                  for value in (admin_key, admin_secret, old_key, old_secret)))
        checks.append("refuse-foreign")

        # An unmatched Vaultwarden key does not silently rotate; explicit input does.
        server, thread = initial_opnsense(owned=True, legacy=True, existing_key=True)
        servers.append((server, thread))
        mismatch_dir = root / "mismatch"
        mismatch_dir.mkdir()
        mismatch, _ = run_account(server, mismatch_dir,
                                  {"api_key": old_key, "api_secret": old_secret})
        check("mismatched key fails without explicit rotation",
              mismatch.returncode != 0 and "opnsense_dns_rotate_key=true" in mismatch.stdout)
        check("mismatched key is not rotated implicitly",
              "user/add_api_key" not in server.mutations)
        check("mismatched key is not revoked without explicit rotation",
              "user/del_api_key" not in server.mutations)
        check("mismatch output does not print credentials",
              all(value not in mismatch.stdout + mismatch.stderr
                  for value in (admin_key, admin_secret, old_key, old_secret,
                                existing_owned_key, "fixture-owned-secret")))
        checks.append("mismatch-no-rotation")

        rotate_dir = root / "rotate"
        rotate_dir.mkdir()
        rotated, rotated_state_path = run_account(
            server, rotate_dir, {"api_key": old_key, "api_secret": old_secret}, rotate=True)
        if rotated.returncode != 0:
            last_task = "unknown"
            diagnostic_lines = (rotated.stdout + "\n" + rotated.stderr).splitlines()
            for index, line in enumerate(diagnostic_lines):
                task = re.match(r"TASK \[(.*?)\]", line)
                if task:
                    last_task = task.group(1)
                if "FAILED!" in line or "fatal:" in line or "ERROR!" in line:
                    print(f"rotation failed task: {last_task}")
                if "OPNsense did not revoke prior DNS API keys" in line:
                    print(f"rotation diagnostic: {line.strip()}")
                if "HTTP statuses=" in line or "results=" in line:
                    print(f"rotation status: {line.strip()}")
                if "TASK [OPNsense DNS account | Verify prior keys were revoked]" in line:
                    for detail in diagnostic_lines[index:index + 15]:
                        for secret in (admin_key, admin_secret, old_key, old_secret,
                                       existing_owned_key, "fixture-owned-secret"):
                            detail = detail.replace(secret, "[redacted]")
                        detail = re.sub(r"fixture-new-[A-Za-z0-9-]+", "[redacted]", detail)
                        print(f"rotation detail: {detail.strip()}")
        rotated_state = read_state(rotated_state_path)
        rotated_fields = {field["name"]: field["value"]
                          for field in rotated_state["items"][0].get("fields", [])}
        new_owned_key = next((value for value in server.api_keys
                              if value["username"] == account_name
                              and value["key"] != existing_owned_key), {})
        check("explicit rotation succeeds and stores the replacement pair",
              rotated.returncode == 0
              and rotated_fields.get("api_key") == new_owned_key.get("key")
              and rotated_fields.get("api_secret") == new_owned_key.get("secret"))
        check("explicit rotation revokes only prior keys for the DNS account",
              [value["key"] for value in server.api_keys
               if value["username"] == account_name] == [new_owned_key.get("key")]
              and any(value["username"] == "homelab-green-dns"
                      and value["key"] == old_key for value in server.api_keys))
        check("prior key path encodes reserved URL characters",
              any("/api/auth/user/del_api_key/" in path
                  and "%2B" in path and "%2F" in path
                  for _method, path, _username, _status in server.calls))
        check("rotated run does not print fixture credentials",
              all(value not in rotated.stdout + rotated.stderr
                  for value in (admin_key, admin_secret, existing_owned_key,
                                "fixture-owned-secret", old_key, old_secret,
                                new_owned_key.get("key", ""),
                                new_owned_key.get("secret", ""))))
        checks.append("explicit-rotation")

        # The canonical hyphenated item name is loaded as its own runtime contract key.
        contract = subprocess.run(
            [str(python), str(repo / "ansible/scripts/vault-runtime.py")],
            input=json.dumps([{"name": "homelab-infra/firewall-admin", "fields": [
                {"name": "api_key", "value": "fixture-admin-key"},
                {"name": "api_secret", "value": "fixture-admin-secret"},
            ]}]), capture_output=True, text=True, check=False,
        )
        parsed = json.loads(contract.stdout) if contract.returncode == 0 else {}
        check("firewall-admin canonical item maps into the runtime contract",
              parsed.get("firewall-admin", {}).get("api_key") == "fixture-admin-key"
              and parsed.get("firewall-admin", {}).get("api_secret") == "fixture-admin-secret")
        checks.append("vault-contract")

    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    for case in checks:
        print(f"== ok   OPNsense DNS account {case}")
    if failures:
        for failure in failures:
            print(f"== FAIL {failure}")
        raise SystemExit(1)
    print("OPNsense DNS account lifecycle checks passed")


if __name__ == "__main__":
    main()
