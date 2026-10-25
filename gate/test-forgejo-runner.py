#!/usr/bin/env python3
"""Check Forgejo Runner startup and removal API scopes."""

import http.server
import hashlib
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
remove_tasks = yaml.safe_load(
    (repo / "ansible/tasks/app-wiring/forgejo-runner-remove.yml").read_text()
)
entrypoint = repo / "ansible/roles/forgejo-runner/files/register-and-run.sh"
reconcile_tasks = repo / "ansible/roles/forgejo-runner/tasks/reconcile-registration.yml"
runner_labels = "docker:docker://docker.io/library/alpine:3.20"


class RunnerApiState:
    def __init__(self, registrations_by_scope, fail_scopes=None, version="16.0.0"):
        self.registrations_by_scope = registrations_by_scope
        self.fail_scopes = set(fail_scopes or [])
        self.version = version
        self.deletes = []
        self.authorizations = []


class RunnerApi(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        state = self.server.state
        parts = self.path.strip("/").split("/")
        if parts == ["api", "v1", "version"]:
            body = json.dumps({"version": state.version}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        state.authorizations.append(self.headers.get("Authorization", ""))
        if len(parts) != 5 or parts[:2] != ["api", "v1"] or parts[3:] != ["actions", "runners"]:
            self.send_error(404)
            return
        scope = parts[2]
        if scope in state.fail_scopes:
            self.send_error(403)
            return
        body = json.dumps({"runners": state.registrations_by_scope.get(scope, [])}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        state = self.server.state
        state.authorizations.append(self.headers.get("Authorization", ""))
        state.deletes.append(self.path)
        parts = self.path.strip("/").split("/")
        if len(parts) == 6 and parts[:2] == ["api", "v1"] and parts[3:5] == ["actions", "runners"]:
            scope, runner_id = parts[2], parts[5]
            if any(
                str(runner["id"]) == runner_id and runner["name"] == "forgejo-runner"
                for runner in state.registrations_by_scope.get(scope, [])
            ):
                state.registrations_by_scope[scope] = [
                    runner for runner in state.registrations_by_scope[scope]
                    if str(runner["id"]) != runner_id
                ]
                self.send_response(204)
                self.end_headers()
                return
        self.send_error(404)

    def log_message(self, *_args):
        pass


def run_removal(registration_scope, token_field, include_admin_fallback=False):
    token = f"fixture-{token_field.removesuffix('_api_token')}-token"
    secrets = {token}
    vault_fields = {token_field: token}
    if include_admin_fallback:
        vault_fields["admin_api_token"] = "fixture-admin-token"
        secrets.add(vault_fields["admin_api_token"])
    registrations_by_scope = {
        "user": ([{"id": 41, "name": "forgejo-runner"}, {"id": 42, "name": "other-runner"}]
                 if registration_scope == "user" else [{"id": 42, "name": "other-runner"}]),
        "admin": ([{"id": 41, "name": "forgejo-runner"}, {"id": 42, "name": "other-runner"}]
                  if registration_scope == "admin" else []),
    }
    state = RunnerApiState(registrations_by_scope)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RunnerApi)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="forgejo-runner-remove-") as temp:
            playbook = Path(temp) / "remove.yml"
            playbook.write_text(yaml.safe_dump([{
                "name": "Forgejo Runner removal test",
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "vars": {
                    "runner_remove_instance": "forgejo-runner",
                    "runner_remove_forgejo_instance": "forgejo",
                    "homelabinfra_infra": {
                        "apps": {"forgejo": {"url": f"http://127.0.0.1:{server.server_port}/"}}
                    },
                    "homelabinfra_vault": {
                        "apps": {"forgejo": vault_fields}
                    },
                },
                "tasks": remove_tasks,
            }]))
            result = subprocess.run(
                [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                env=os.environ | {"ANSIBLE_NOCOLOR": "1"},
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            assert result.returncode == 0, output
            assert all(secret not in output for secret in secrets), (
                "API token appeared in Ansible output"
            )
        expected = f"/api/v1/{registration_scope}/actions/runners/41"
        assert state.deletes == [expected], state.deletes
        if registration_scope == "admin" and token_field == "runner_api_token":
            expected_authorizations = [
                f"token {token}",
                "token fixture-admin-token",
                "token fixture-admin-token",
            ]
        elif registration_scope == "admin":
            expected_authorizations = [f"token {token}", f"token {token}"]
        else:
            expected_authorizations = [f"token {token}", f"token {token}"]
        assert state.authorizations == expected_authorizations, state.authorizations
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def registration_marker(url, token, name="forgejo-runner", labels=runner_labels):
    values = (url, token, name, labels)
    component_digests = [hashlib.sha256(value.encode()).hexdigest() for value in values]
    fingerprint = hashlib.sha256(":".join(component_digests).encode()).hexdigest()
    return f"v1:{fingerprint}\n"


def check_entrypoint_registration_fingerprint():
    with tempfile.TemporaryDirectory(prefix="forgejo-runner-startup-") as temp:
        root = Path(temp)
        data = root / "data"
        data.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        call_log = root / "calls.log"
        fake = bin_dir / "forgejo-runner"
        fake.write_text(r"""#!/bin/sh
set -eu
case "$1" in
  generate-config) printf 'runner: {}\n' ;;
  register)
    printf 'register\n' >> "$RUNNER_FAKE_LOG"
    printf '{"id":1,"uuid":"fixture","name":"%s","token":"runtime-secret","address":"%s"}\n' \
      "$FORGEJO_RUNNER_NAME" "$FORGEJO_INSTANCE_URL" > .runner
    ;;
  daemon) printf 'daemon\n' >> "$RUNNER_FAKE_LOG" ;;
  *) exit 2 ;;
esac
""")
        fake.chmod(0o755)
        environment = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FORGEJO_RUNNER_DATA_DIR": str(data),
            "RUNNER_FAKE_LOG": str(call_log),
            "FORGEJO_INSTANCE_URL": "http://forgejo-a.invalid/",
            "FORGEJO_RUNNER_TOKEN": "registration-secret-a",
            "FORGEJO_RUNNER_NAME": "forgejo-runner",
            "FORGEJO_RUNNER_LABELS": runner_labels,
        }

        def start():
            result = subprocess.run(
                ["/bin/sh", str(entrypoint)],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            assert result.returncode == 0, output
            assert "registration-secret-a" not in output
            return call_log.read_text().splitlines()

        assert start() == ["register", "daemon"]
        marker = data / ".homelab-infra-registered"
        assert marker.read_text() == registration_marker(
            environment["FORGEJO_INSTANCE_URL"], environment["FORGEJO_RUNNER_TOKEN"]
        )
        assert "registration-secret-a" not in marker.read_text()

        assert start() == ["register", "daemon", "daemon"]
        environment["FORGEJO_INSTANCE_URL"] = "http://forgejo-b.invalid/"
        assert start() == ["register", "daemon", "daemon", "register", "daemon"]
        environment["FORGEJO_RUNNER_TOKEN"] = "registration-secret-b"
        assert start() == [
            "register", "daemon", "daemon", "register", "daemon", "register", "daemon"
        ]
        (data / ".runner").unlink()
        assert start()[-2:] == ["register", "daemon"]


def run_reconciliation(marker_url, marker_token, current_token, registrations, expected_deletes,
                       keep_local_registration, fail_scopes=None, server_version="16.0.0"):
    state = RunnerApiState(
        {"user": [], "admin": registrations},
        fail_scopes=fail_scopes,
        version=server_version,
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RunnerApi)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        current_url = f"http://127.0.0.1:{server.server_port}/"
        saved_url = current_url if marker_url is None else marker_url
        with tempfile.TemporaryDirectory(prefix="forgejo-runner-reconcile-") as temp:
            data = Path(temp)
            runner_file = data / ".runner"
            runner_file.write_text(json.dumps({
                "id": 41,
                "uuid": "fixture-uuid",
                "name": "forgejo-runner",
                "token": "saved-runner-secret",
                "address": saved_url,
                "labels": [runner_labels],
            }))
            marker = data / ".homelab-infra-registered"
            marker.write_text(registration_marker(saved_url, marker_token))
            playbook = data / "reconcile.yml"
            playbook.write_text(yaml.safe_dump([{
                "name": "Forgejo Runner registration reconciliation test",
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "vars": {
                    "instance": "forgejo-runner",
                    "app_config": {"app": {
                        "forgejo": {"instance": "forgejo"},
                        "forgejo_url": current_url,
                        "runner_name": "forgejo-runner",
                        "labels": runner_labels,
                        "registration_marker_path": str(marker),
                        "registration_runner_path": str(runner_file),
                    }},
                    "_forgejo_runner_token": current_token,
                    "homelabinfra_vault": {"apps": {"forgejo": {
                        "admin_api_token": "fixture-admin-token",
                    }}},
                },
                "tasks": [
                    {"ansible.builtin.include_tasks": str(reconcile_tasks)},
                ],
                "handlers": [{
                    "name": "Restart Forgejo Runner",
                    "ansible.builtin.debug": {"msg": "fixture restart"},
                }],
            }]))
            result = subprocess.run(
                [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                env=os.environ | {"ANSIBLE_NOCOLOR": "1"},
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            if fail_scopes:
                assert result.returncode != 0, output
                assert "admin=403" in output, output
                assert state.deletes == [], state.deletes
                assert runner_file.exists()
                assert marker.exists()
            else:
                assert result.returncode == 0, output
                assert state.deletes == expected_deletes, (state.deletes, output)
                assert runner_file.exists() is keep_local_registration
                assert marker.exists() is keep_local_registration
            assert "fixture-admin-token" not in output
            assert "saved-runner-secret" not in output
        expected_authorizations = (
            ["token fixture-admin-token"] * (1 + len(expected_deletes))
            if int(server_version.split(".", 1)[0]) >= 16
            else []
        )
        assert state.authorizations == expected_authorizations, state.authorizations
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def check_registration_reconciliation():
    current_token = "registration-secret-current"

    # An unchanged, known identity stays in place, and only same-name duplicates are removed.
    run_reconciliation(
        None,
        current_token,
        current_token,
        [
            {"id": 41, "name": "forgejo-runner"},
            {"id": 43, "name": "forgejo-runner"},
            {"id": 42, "name": "other-runner"},
        ],
        ["/api/v1/admin/actions/runners/43"],
        True,
    )

    # A registration-token change removes only this runner's old server registration.
    run_reconciliation(
        None,
        "registration-secret-old",
        current_token,
        [
            {"id": 41, "name": "forgejo-runner"},
            {"id": 42, "name": "other-runner"},
        ],
        ["/api/v1/admin/actions/runners/41"],
        False,
    )

    # A changed URL or a Forgejo that forgot the saved ID causes a clean registration.
    run_reconciliation(
        "http://old-forgejo.invalid/",
        current_token,
        current_token,
        [
            {"id": 41, "name": "forgejo-runner"},
            {"id": 42, "name": "other-runner"},
        ],
        ["/api/v1/admin/actions/runners/41"],
        False,
    )
    run_reconciliation(
        None,
        current_token,
        current_token,
        [{"id": 42, "name": "other-runner"}],
        [],
        False,
    )

    # An unavailable API does not discard local registration state.
    run_reconciliation(
        None,
        current_token,
        current_token,
        [{"id": 41, "name": "forgejo-runner"}],
        [],
        True,
        fail_scopes={"admin"},
    )

    # Older Forgejo releases have no runner listing API, so an unchanged local identity
    # remains idempotent until the server is upgraded to a version that can verify it.
    run_reconciliation(
        None,
        current_token,
        current_token,
        [{"id": 41, "name": "forgejo-runner"}],
        [],
        True,
        server_version="14.0.0+gitea-1.22.0",
    )


def check_non_root_entrypoint_access():
    role_tasks = yaml.safe_load(
        (repo / "ansible/roles/forgejo-runner/tasks/main.yml").read_text()
    )
    entrypoint = next(
        task["ansible.builtin.copy"]
        for task in role_tasks
        if task.get("name") == "Forgejo Runner | Install idempotent registration entrypoint"
    )
    compose = (
        repo / "ansible/roles/forgejo-runner/templates/docker-compose.yml.j2"
    ).read_text()
    assert entrypoint["owner"] == "1000"
    assert entrypoint["group"] == "1000"
    assert entrypoint["mode"] == "0550"
    assert 'user: "1000:1000"' in compose
    assert 'entrypoint: ["/opt/runner/register-and-run.sh"]' in compose


def check_runner_service_running_retries():
    role_tasks = yaml.safe_load(
        (repo / "ansible/roles/forgejo-runner/tasks/main.yml").read_text()
    )
    check = next(
        task
        for task in role_tasks
        if task.get("name") == "Forgejo Runner | Check that the runner service is running"
    )
    assert check["retries"] == 12
    assert check["delay"] == 5
    assert check["ansible.builtin.command"]["argv"][-4:] == [
        "ps", "--status", "running", "--services"
    ]
    assert "runner" in check["until"]
    assert "stdout_lines" in check["until"]


def main():
    check_non_root_entrypoint_access()
    check_runner_service_running_retries()
    check_entrypoint_registration_fingerprint()
    check_registration_reconciliation()
    run_removal("user", "runner_api_token", include_admin_fallback=True)
    run_removal("admin", "admin_api_token")
    run_removal("admin", "runner_api_token", include_admin_fallback=True)
    print("Forgejo Runner registration and removal checks passed")


if __name__ == "__main__":
    main()
