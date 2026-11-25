#!/usr/bin/env python3
"""Check Immich's Kubernetes hosting and shared recovery wiring.

Checks: Docker stays the default method, Kubernetes is supported, the media claim uses the
cluster's default StorageClass, machine learning is pinned to the iGPU node and gets
/dev/dri only when a GPU is declared, the registered backends are reached through the
cluster-node alias, and backup and restore go through the shared Kubernetes operations.
"""

import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")


def load(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def render(gpu):
    text = (ROOT / "ansible/roles/immich/templates/manifest.yaml.j2").read_text(encoding="utf-8")
    variables = {
        "instance": "immich",
        "_deploy_fqdn": "immich.example.test",
        "immich_k8s_media_claim": "immich-media",
        "immich_k8s_ml_port": 3003,
        "_immich_k8s_env_checksum": "abc",
        "_immich_gpu_declared": gpu,
        "_immich_gpu_nodes": ["k3s-4"],
        "app_config": {
            "media_storage": {"owner": {"uid": 1313, "gid": 1313}},
            "app": {"image": "server:v1", "machine_learning_image": "ml:v1-openvino",
                    "port": 2283, "storage": 20},
        },
    }
    return list(yaml.safe_load_all(Templar(loader=DataLoader(), variables=variables).template(text)))


def check_defaults_and_dispatch():
    defaults = load("ansible/vars/app-defaults/immich.yml")["immich_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes"]
    assert [(e["backend"], e["protocol"]) for e in defaults["firewall"]["egress"]] == [
        ("database", "TCP"), ("redis", "TCP")]
    assert defaults["firewall"]["methods"] == ["kubernetes"]
    restore = load("ansible/roles/immich/tasks/restore.yml")
    assert [t["ansible.builtin.include_tasks"] for t in restore] == [
        "restore-kubernetes.yml", "restore-guest.yml"]
    assert "== 'kubernetes'" in restore[0]["when"] and "== 'docker'" in restore[1]["when"]
    prepare = load("ansible/roles/immich/tasks/deploy-prepare.yml")
    docker_only = [t for t in prepare if "'docker'" in str(t.get("when", ""))]
    assert len(docker_only) == len(prepare) - 1, "only the GPU check is shared with Kubernetes"


def check_workload():
    for gpu in (True, False):
        docs = render(gpu)
        claim = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
        assert claim["spec"]["resources"]["requests"]["storage"] == "20Gi"
        assert "storageClassName" not in claim["spec"]
        deployments = {d["metadata"]["name"]: d["spec"]["template"]["spec"]
                       for d in docs if d["kind"] == "Deployment"}
        assert set(deployments) == {"immich", "immich-machine-learning"}
        server = deployments["immich"]
        assert server["securityContext"]["runAsUser"] == 1313
        assert [m["mountPath"] for m in server["containers"][0]["volumeMounts"]] == ["/data"]
        ml = deployments["immich-machine-learning"]
        assert ml["containers"][0]["image"] == "ml:v1-openvino"
        assert ("nodeSelector" in ml) == gpu
        assert ("securityContext" in ml["containers"][0]) == gpu
        if gpu:
            assert ml["nodeSelector"] == {"kubernetes.io/hostname": "k3s-4"}
            assert {"name": "dri", "mountPath": "/dev/dri"} in ml["containers"][0]["volumeMounts"]
        ingress = next(d for d in docs if d["kind"] == "Ingress")
        assert ingress["spec"]["rules"][0]["host"] == "immich.example.test"
        assert "storageClassName" not in str(docs)


def check_shared_recovery_inputs():
    deploy = load("ansible/roles/immich/tasks/kubernetes.yml")
    text = str(deploy)
    for shared in ("tasks/kubernetes/apply-manifest.yml", "tasks/kubernetes/recovery-backup.yml",
                   "tasks/database/kubernetes-recovery-step.yml"):
        assert shared in text
    assert "kubectl" not in text
    step = next(t for t in deploy if "kubernetes-recovery-step" in str(t))
    assert step["vars"]["k8s_database_step_source"] == "registered"
    backup = next(t for t in deploy if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml"))
    recovery = backup["vars"]["k8s_recovery"]
    assert recovery["archives"][0]["name"] == "media", "media.pxar matches the Docker recovery point"
    assert recovery["archives"][0]["claim"] == "{{ immich_k8s_media_claim }}"
    assert recovery["stop"] == ["{{ instance }}"]

    restore = load("ansible/roles/immich/tasks/restore-kubernetes.yml")
    job = next(t for t in restore if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml"))
    inputs = job["vars"]["k8s_recovery"]
    assert inputs["archives"][0]["claim"] == "{{ restore_target }}-media"
    assert inputs["stop"] == ["{{ restore_target }}"]
    # app_config is the source's in a restore: the owner must come from the target config.
    owner = inputs["archives"][0]["owner"]
    assert "restore_target_config.media_storage.owner" in owner and "app_config" not in owner
    variables = {"app_config": {"media_storage": {"owner": {"uid": 1, "gid": 2}}},
                 "restore_target_config": {"media_storage": {"owner": {"uid": 1313, "gid": 1414}}}}
    assert Templar(loader=DataLoader(), variables=variables).template(owner) == "1313:1414"
    assert next(t for t in restore if "kubernetes-recovery-step" in str(t))["vars"][
        "k8s_database_step_source"] == "registered"
    role = ROOT / "ansible/roles/immich/templates"
    assert not (role / "backup-cronjob.yaml.j2").exists() and not (role / "restore-job.yaml.j2").exists()


def main():
    check_defaults_and_dispatch()
    check_workload()
    check_shared_recovery_inputs()
    print("Immich Kubernetes: hosting dispatch, workload and shared recovery contract passed")


if __name__ == "__main__":
    main()
