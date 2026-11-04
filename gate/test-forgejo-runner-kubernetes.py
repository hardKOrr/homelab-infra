#!/usr/bin/env python3
"""Check Forgejo Runner's Docker default and its Kubernetes hosting contract."""

from pathlib import Path
import subprocess
import tempfile

import jinja2

import yaml

REPO = Path(__file__).resolve().parents[1]
ROLE = REPO / "ansible/roles/forgejo-runner"


def read_yaml(path):
    return yaml.safe_load(Path(path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def check_saved_file_framing():
    """Run the in-pod reader on populated and missing files and parse it as the role does."""
    read, expose = read_yaml(ROLE / "tasks/registration-files-kubernetes.yml")[1:3]
    command = read["ansible.builtin.command"]
    assert command["strip_empty_ends"] is False
    assert "strip_empty_ends" not in read
    argv = command["argv"]
    script = argv[len(argv) - argv[::-1].index("-c")]
    facts = expose["ansible.builtin.set_fact"]
    env = jinja2.Environment()
    env.filters["b64encode"] = lambda v: __import__("base64").b64encode(v.encode()).decode()
    with tempfile.TemporaryDirectory() as directory:
        marker, record = Path(directory, "marker"), Path(directory, "runner")
        for saved, want_marker, want_record in (
            (True, "v1:abc", '{\n  "id": 7\n}'),
            (False, "", "{}"),
        ):
            for path in (marker, record):
                path.unlink(missing_ok=True)
            if saved:
                marker.write_text("v1:abc\n")
                record.write_text('{\n  "id": 7\n}\n')
            out = subprocess.run(["sh", "-c", script, "sh", str(marker), str(record)],
                                 capture_output=True, text=True, check=True).stdout
            # Ansible's command module strips trailing blank lines unless told otherwise.
            ctx = {"_forgejo_runner_k8s_files": {"stdout": out}}
            got = {
                key: __import__("base64").b64decode(
                    env.from_string(value["content"]).render(**ctx)).decode()
                for key, value in facts.items() if isinstance(value, dict)
            }
            assert got["_forgejo_runner_registration_marker_file"] == want_marker, got
            assert got["_forgejo_runner_registration_record_file"] == want_record, got


def main():
    defaults = read_yaml(REPO / "ansible/vars/app-defaults/forgejo-runner.yml")["forgejo_runner_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert "kubernetes" in defaults["hosting"]["supported"]
    assert defaults["routing"] == {"proxy": "none", "access": "internal", "identity": "none"}
    # The shared backup scheduler renders these required inputs even in a stateless drill.
    assert defaults["backup"]["retention_days"] > 0
    assert {"image", "key_url", "repository", "suite", "component"} <= defaults["backup"]["client"].keys()

    plays = {play["name"]: play for play in read_yaml(REPO / "ansible/playbooks/apps/forgejo-runner.yml")}
    provision = plays["Forgejo Runner | Provision"]["tasks"]
    stack = next(t for t in provision if "find-or-create-host" in str(t.get("ansible.builtin.import_tasks")))
    assert stack["when"] == "app_config.hosting.method == 'docker'"
    assert includes(plays["Forgejo Runner | Prepare Kubernetes"]["tasks"], "tasks/kubernetes/open-app-access.yml")
    deploy = plays["Forgejo Runner | Deploy"]["tasks"]
    assert all("hostvars['localhost'].app_config.hosting.method == 'docker'" in str(t.get("when")) for t in deploy)
    kube = plays["Forgejo Runner | Deploy on Kubernetes"]["tasks"][0]
    assert kube["ansible.builtin.include_role"]["tasks_from"] == "kubernetes"
    assert kube["when"] == "app_config.hosting.method == 'kubernetes'"

    tasks = read_yaml(ROLE / "tasks/kubernetes.yml")
    for shared in ("resolve-cluster", "ensure-namespace", "sync-secret", "apply-manifest", "recovery-backup"):
        assert includes(tasks, f"tasks/kubernetes/{shared}.yml"), shared
    assert includes(tasks, "registration.yml")
    backup = next(t for t in tasks if "recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    assert backup["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ instance }}-data"
    # Docker and Kubernetes share the registration steps.
    assert includes(read_yaml(ROLE / "tasks/main.yml"), "registration.yml")
    for name in ("files", "clear"):
        for method in ("host", "kubernetes"):
            assert (ROLE / f"tasks/registration-{name}-{method}.yml").is_file()

    check_saved_file_framing()

    manifest = (ROLE / "templates/manifest.yaml.j2").read_text()
    for needle in (
        "privileged: true",
        "kind: PersistentVolumeClaim",
        "tcp://localhost:2375",
        "secretKeyRef",
        "register-and-run.sh",
        "{{ _forgejo_runner_registration_fingerprint }}",
    ):
        assert needle in manifest, needle
    # No port, route or storageClassName: the cluster default class backs the volume.
    for absent in ("kind: Service", "kind: Ingress", "storageClassName"):
        assert absent not in manifest, absent
    print("Forgejo Runner Kubernetes contract passed.")


if __name__ == "__main__":
    main()
