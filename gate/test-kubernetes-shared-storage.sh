#!/usr/bin/env bash
# Focused static contract checks for tracker issue #46's opt-in NFS CSI storage foundation.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
fail() { echo "kubernetes-shared-storage test failed: $*" >&2; exit 1; }

defaults="$repo/ansible/vars/app-defaults/k3s-cluster.yml"
example="$repo/config.example/apps/k3s-cluster.example.yml"
storage_tasks="$repo/ansible/roles/k3s_cluster/tasks/storage.yml"

python3 - "$defaults" "$example" <<'PY'
import sys
import yaml

defaults = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
example = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
shared = defaults["k3s_cluster_defaults"]["shared_storage"]
assert shared["enabled"] is False
assert shared["class"] != defaults["k3s_cluster_defaults"]["storage"]["class"]
assert shared["driver"] == "nfs.csi.k8s.io"
assert shared["chart_version"] == "4.13.4"
assert shared["reclaim_policy"] == "Retain"
assert shared["allow_volume_expansion"] is False
assert shared["controller_replicas"] == 2
assert shared["snapshot"]["deletion_policy"] == "Retain"
assert example["shared_storage"]["enabled"] is False
assert example["shared_storage"]["target"]["failure_domains"]
PY

# The shared class must stay opt-in and non-default. Existing local-path PVCs therefore
# cannot be silently moved by either the StorageClass template or its convergence task.
grep -Fq 'storageclass.kubernetes.io/is-default-class' \
  "$repo/ansible/roles/k3s_cluster/templates/storageclass.yaml.j2" \
  || fail "local StorageClass no longer declares its default annotation"
if grep -Fq 'storageclass.kubernetes.io/is-default-class' \
  "$repo/ansible/roles/k3s_cluster/templates/shared-nfs-storageclass.yaml.j2"; then
  fail "shared NFS StorageClass must not be default"
fi

for required in \
  'k3s shared storage | Assert the opted-in NFS CSI contract is complete' \
  'k3s shared storage | Wait for the NFS CSI controller' \
  'k3s shared storage | Wait for the NFS CSI node plugin' \
  'k3s shared storage | Declare the non-default NFS StorageClass' \
  'k3s shared storage | Declare the retained NFS snapshot class'; do
  grep -Fq -- "$required" "$storage_tasks" || fail "missing convergence safeguard: $required"
done

grep -Fq 'k3s shared storage | Resolve nodes eligible to run CSI controllers' "$storage_tasks" \
  || fail "CSI controller preflight does not resolve schedulable nodes"
grep -Fq "selectattr('taints', 'equalto', [])" "$storage_tasks" \
  || fail "CSI controller preflight does not exclude tainted nodes"
if grep -Fq '<= k8s_cluster_config.cluster.nodes | length' "$storage_tasks"; then
  fail "CSI controller preflight counts total nodes instead of schedulable nodes"
fi

grep -Fq 'k8s_cluster_config.shared_storage.driver' "$repo/ansible/roles/k3s_cluster/templates/shared-nfs-csi-driver.yaml.j2" \
  || fail "CSI driver template does not use the supported NFS CSI identity"
grep -Fq 'podAntiAffinity:' "$repo/ansible/roles/k3s_cluster/templates/shared-nfs-csi-driver.yaml.j2" \
  || fail "CSI controller replicas are not anti-affined"
grep -Fq 'onDelete: retain' "$repo/ansible/roles/k3s_cluster/templates/shared-nfs-storageclass.yaml.j2" \
  || fail "NFS directory deletion is not explicitly retained"
grep -Fq 'deletionPolicy: {{ k8s_cluster_config.shared_storage.snapshot.deletion_policy }}' \
  "$repo/ansible/roles/k3s_cluster/templates/shared-nfs-volumesnapshotclass.yaml.j2" \
  || fail "snapshot class does not use the retained policy"

for required in \
  'Migration and rollback' \
  'Eligibility and required live drill' \
  'Do not edit or delete an existing PVC' \
  'not make a single-replica application or database highly available.'; do
  grep -Fq -- "$required" "$repo/ansible/tasks/kubernetes/README.md" \
    || fail "backend guide omits: $required"
done

echo "kubernetes-shared-storage test: PASS"
