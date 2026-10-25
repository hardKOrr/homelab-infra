#!/usr/bin/env python3
"""Keep the explicit k3s cluster removal workflow ordered and ownership-scoped."""

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text(encoding="utf-8"))


def main():
    remove = read_yaml("ansible/playbooks/apps/remove-k3s-cluster.yml")
    resolve = remove[0]
    confirmation = next(task for task in resolve["pre_tasks"]
                        if task.get("name") == "Assert the selected cluster and destructive confirmation")
    assert any("REMOVE-K3S-CLUSTER" in condition
               for condition in confirmation["ansible.builtin.assert"]["that"]), confirmation
    assert any(task.get("ansible.builtin.include_tasks")
               == "../../tasks/kubernetes/remove-cluster.yml"
               for task in resolve["tasks"]), resolve

    lifecycle = read_yaml("ansible/tasks/kubernetes/remove-cluster.yml")
    names = [task.get("name", "") for task in lifecycle]
    ordered = [
        "k3s Remove | Verify the firewall alias before destroying node VMs",
        "k3s Remove | Refuse foreign, shared-workload or changed guests before teardown",
        "k3s Remove | Require app namespaces to be removed first",
        "k3s Remove | Mark the selected registry as removing",
        "k3s Remove | Destroy each verified cluster node VM",
        "k3s Remove | Delete the cluster's owned firewall alias",
        "k3s Remove | Remove the selected cluster from generated topology",
    ]
    positions = [names.index(name) for name in ordered]
    assert positions == sorted(positions), (names, positions)

    preflight = lifecycle[names.index("k3s Remove | Refuse foreign, shared-workload or changed guests before teardown")]
    assert any("_.cluster+k3s" in condition
               for condition in preflight["ansible.builtin.assert"]["that"]), preflight
    assert any("^_[A-Za-z0-9]" in condition
               for condition in preflight["ansible.builtin.assert"]["that"]), preflight

    destroy = read_yaml("ansible/tasks/proxmox/destroy-owned-vm.yml")
    identity = next(task["ansible.builtin.assert"] for task in destroy
                    if task.get("name") == "Destroy owned VM | Validate the original guest identity")
    live = next(task["ansible.builtin.assert"] for task in destroy
                if task.get("name") ==
                "Destroy owned VM | Require current lab ownership and matching managed tags")
    assert any("destroy_vm_owner_tag" in condition for condition in identity["that"]), identity
    assert any("destroy_vm_owner_tag" in condition for condition in live["that"]), live

    resolve_cluster = read_yaml("ansible/tasks/kubernetes/resolve-cluster.yml")
    assert any(task.get("name") ==
               "k8s resolve | Refuse new cluster operations while cluster Remove is in progress"
               for task in resolve_cluster), resolve_cluster
    unwire = read_yaml("ansible/tasks/unwiring/kubernetes.yml")
    allow_remove = next(task for task in unwire
                        if task.get("name") == "k8s unwire | Resolve the cluster")
    assert allow_remove["vars"]["k8s_resolve_allow_removing"] is True, allow_remove

    actions = read_yaml("rundeck/app-actions.yml")["actions"]
    catalog = read_yaml("catalog/applications.yml")["applications"]["k3s-cluster"]
    assert "remove_k3s_cluster" in catalog["actions"], catalog
    assert actions["remove_k3s_cluster"]["template"] == "remove-k3s-cluster.yaml", actions
    job = read_yaml("rundeck/jobs/remove-k3s-cluster.yaml")[0]
    confirm = next(option for option in job["options"] if option["name"] == "confirm")
    assert confirm["required"] is True and "REMOVE-K3S-CLUSTER" in confirm["description"], confirm
    assert "local-path" in job["description"] and "PBS backups" in job["description"], job

    docs = (REPO / "ansible/tasks/kubernetes/README.md").read_text(encoding="utf-8")
    assert "homelabinfra_k3s_<cluster>" in docs, "README lost the node alias contract"
    assert "Remove k3s Cluster" in docs and "retained local-path volumes" in docs, docs

    print("k3s cluster removal: ok")


if __name__ == "__main__":
    main()
