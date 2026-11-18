#!/usr/bin/env python3
"""Exercise the shared role building blocks without a Docker host or a cluster.

- templates/kubernetes/single-container.yaml.j2 renders a minimal and a full `k8s_app`
  into the objects its header promises.
- tasks/stack/compose-deploy.yml renders the role's Compose file and mounted files, and a
  failed render prints why even with compose_secret_files hiding the content.
- compose-deploy.yml, tasks/apps/restart.yml and tasks/kubernetes/single-container.yml
  refuse bad inputs in their first task, with their message.

gate/test-shared-standards.py checks which roles still carry their own copies.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
MANIFEST = ROOT / "ansible/templates/kubernetes/single-container.yaml.j2"
COMPOSE = ROOT / "ansible/tasks/stack/compose-deploy.yml"
RESTART = ROOT / "ansible/tasks/apps/restart.yml"
SINGLE = ROOT / "ansible/tasks/kubernetes/single-container.yml"


def render_manifest(app):
    variables = {"instance": "demo", "k8s_app": app}
    text = Templar(loader=DataLoader(), variables=variables).template(MANIFEST.read_text())
    return {doc["kind"]: doc for doc in yaml.safe_load_all(text) if doc}, text


def check_minimal_manifest():
    objects, _ = render_manifest({"name": "demo", "image": "example/demo:1", "port": 8080})
    assert set(objects) == {"Deployment", "Service"}, sorted(objects)
    deployment = objects["Deployment"]
    assert "strategy" not in deployment["spec"], "no volume, no Recreate"
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    assert container["ports"] == [{"containerPort": 8080}]
    assert "volumeMounts" not in container and "volumes" not in pod
    assert container["resources"] == {"requests": {"cpu": "100m", "memory": "256Mi"}}
    service = objects["Service"]["spec"]
    assert "type" not in service and service["ports"] == [{"port": 8080, "targetPort": 8080}]
    for obj in objects.values():
        assert obj["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "homelab-infra"


def check_full_manifest():
    objects, text = render_manifest({
        "name": "demo",
        "image": "example/demo:1",
        "port": 80,
        "container_port": 8181,
        "host": "demo.lab.example",
        "node_port": 30080,
        "env": [{"name": "TZ", "value": "UTC"}, {"name": "PORT", "value": 8181},
                {"name": "TOKEN", "secret": "api-token"}],
        "volumes": [{"name": "config", "mount_path": "/config", "storage": 2},
                    {"name": "media", "mount_path": "/media", "read_only": True,
                     "nfs": {"server": "192.0.2.10", "path": "/export/media"}}],
        "extra_volumes": [{"name": "music", "nfs": {"server": "192.0.2.10", "path": "/m"}}],
        "extra_volume_mounts": [{"name": "music", "mountPath": "/music", "readOnly": True}],
        "probe": {"path": "/status"},
        "security_context": {"run_as_user": 1001},
        "pod_annotations": {"checksum/credentials": "abc"},
        "image_pull_policy": "Always",
    })
    assert set(objects) == {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"}, text
    assert objects["PersistentVolumeClaim"]["metadata"]["name"] == "demo-config"
    assert objects["PersistentVolumeClaim"]["spec"]["resources"]["requests"]["storage"] == "2Gi"
    deployment = objects["Deployment"]["spec"]
    assert deployment["strategy"] == {"type": "Recreate"}
    pod = deployment["template"]["spec"]
    assert deployment["template"]["metadata"]["annotations"] == {"checksum/credentials": "abc"}
    assert pod["securityContext"]["runAsGroup"] == 1001 and pod["securityContext"]["fsGroup"] == 1001
    container = pod["containers"][0]
    assert container["imagePullPolicy"] == "Always"
    assert container["env"][1] == {"name": "PORT", "value": "8181"}, "env values are strings"
    assert container["env"][2]["valueFrom"]["secretKeyRef"] == {"name": "demo", "key": "api-token"}
    assert [m["mountPath"] for m in container["volumeMounts"]] == ["/config", "/media", "/music"]
    assert container["volumeMounts"][1]["readOnly"] is True
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"] == {"path": "/status", "port": 8181}, probe
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["config"]["persistentVolumeClaim"] == {"claimName": "demo-config"}
    assert volumes["media"]["nfs"] == {"server": "192.0.2.10", "path": "/export/media", "readOnly": True}
    assert "music" in volumes
    service = objects["Service"]["spec"]
    assert service["type"] == "NodePort"
    assert service["ports"] == [{"port": 80, "targetPort": 8181, "nodePort": 30080}]
    rule = objects["Ingress"]["spec"]["rules"][0]
    assert rule["host"] == "demo.lab.example"
    assert rule["http"]["paths"][0]["backend"]["service"]["port"]["number"] == 80

    objects, _ = render_manifest({"name": "demo", "image": "x", "port": 53, "probe": {"tcp": True}})
    container = objects["Deployment"]["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["tcpSocket"] == {"port": 53}


def run(tasks, workdir, roles=None):
    playbook = Path(workdir) / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "tasks": tasks, "roles": roles or [],
    }]))
    return subprocess.run(
        [str(ANSIBLE), "-i", "localhost,", "-c", "local", str(playbook)],
        text=True, capture_output=True, check=False, cwd=workdir,
        env={**os.environ, "ANSIBLE_ROLES_PATH": str(Path(workdir) / "roles"),
             "PATH": f"{fake_docker(workdir)}:{os.environ['PATH']}"},
    )


def fake_docker(workdir):
    """A `docker` that always fails, so no run here reaches a real Docker engine."""
    bin_dir = Path(workdir) / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\necho 'fake docker: no engine in the gate' >&2\nexit 1\n")
    docker.chmod(0o755)
    return bin_dir


def demo_role(workdir, compose_vars, compose_text, env_text):
    role = Path(workdir) / "roles/demo"
    (role / "tasks").mkdir(parents=True)
    (role / "templates").mkdir()
    (role / "templates/docker-compose.yml.j2").write_text(compose_text)
    (role / "templates/app.env.j2").write_text(env_text)
    (role / "tasks/main.yml").write_text(yaml.safe_dump([{
        "name": "Deploy",
        "ansible.builtin.include_tasks": str(COMPOSE),
        "vars": compose_vars,
    }]))


def check_compose_renders_then_pulls():
    with tempfile.TemporaryDirectory() as workdir:
        project = Path(workdir) / "project"
        demo_role(workdir, {
            "compose_project_dir": str(project),
            "compose_owner": os.getuid(), "compose_group": os.getgid(),
            "compose_dirs": [str(Path(workdir) / "data")],
            "compose_files": [{"src": "app.env.j2", "dest": str(project / "app.env"),
                               "owner": os.getuid(), "group": os.getgid()}],
        }, "services: {}\n", "TOKEN={{ 'secret' }}\n")
        # Files must be owned by root only on a real host; this run is unprivileged.
        compose = COMPOSE.read_text().replace("owner: root\n    group: root", "owner: \"{{ omit }}\"\n    group: \"{{ omit }}\"")
        assert compose != COMPOSE.read_text(), "the root-owned Compose file task moved"
        local = Path(workdir) / "compose-deploy.yml"
        local.write_text(compose)
        (Path(workdir) / "roles/demo/tasks/main.yml").write_text(
            (Path(workdir) / "roles/demo/tasks/main.yml").read_text().replace(str(COMPOSE), str(local)))
        result = run([], workdir, roles=["demo"])
        output = result.stdout + result.stderr
        assert (project / "app.env").read_text() == "TOKEN=secret\n", output
        assert (project / "docker-compose.yml").read_text() == "services: {}\n", output
        assert (Path(workdir) / "data").is_dir(), output
        # Rendering passed; the fake docker stops the run at the pull.
        assert result.returncode != 0, output
        failed = [line for line in output.splitlines() if line.startswith("TASK [")][-1]
        assert "Compose deploy | Pull the images" in failed, output


def check_compose_render_failure_prints():
    with tempfile.TemporaryDirectory() as workdir:
        demo_role(workdir, {
            "compose_project_dir": str(Path(workdir) / "project"),
            "compose_owner": os.getuid(), "compose_group": os.getgid(),
            "compose_secret_files": True,
        }, "services: {{ undefined_compose_value }}\n", "")
        result = run([], workdir, roles=["demo"])
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert "Rendering failed" in output and "undefined_compose_value" in output, output


def check_inputs_refused():
    cases = [
        (COMPOSE, {"compose_project_dir": "relative/dir"}, "absolute compose_project_dir"),
        (RESTART, {"restart_runtime": "docker"}, "restart_runtime docker"),
        (RESTART, {"restart_runtime": "native", "restart_services": "demo"}, "non-empty restart_services"),
        (SINGLE, {"k8s_app": {"name": "Demo", "image": "x", "port": 80}}, "legal container name"),
        (SINGLE, {"k8s_app": {"name": "demo", "image": "x", "port": 80,
                              "volumes": [{"name": "data", "mount_path": "/d"}]}}, "storage, claim or nfs"),
    ]
    for path, variables, message in cases:
        with tempfile.TemporaryDirectory() as workdir:
            result = run([{"name": "Include", "ansible.builtin.include_tasks": str(path),
                           "vars": variables}], workdir)
            output = result.stdout + result.stderr
            assert result.returncode != 0 and message in output, (path.name, variables, output)


def main():
    check_minimal_manifest()
    check_full_manifest()
    check_compose_renders_then_pulls()
    check_compose_render_failure_prints()
    check_inputs_refused()
    print("role building blocks: manifest, Compose render and input checks pass")


if __name__ == "__main__":
    main()
