# Ollama recovery and GPU authority

Ollama is a single Docker service on its own Debian VM. The VM is not the shared `ai`
LXC because its exact PCI device is dedicated to this guest. The instance configuration
must name the PCI address, and `config/proxmox.yml` must declare that same identifier under
one `mode: dedicated` device entry before deployment. The attach seam refuses a shared or
ambiguous declaration and refuses a device already assigned to another VM.

Before live binding, record:

- the Ollama instance, Proxmox node, VM name/VMID, and AI-stack purpose;
- the exact PCI address and the guest's GPU/console recovery procedure; and
- the model-storage path and its intended capacity.

## Model and configuration recovery

The Ollama role keeps downloaded models and Ollama metadata below `app.storage_path`,
with `app.models_path` as the large persistent mount exposed to the container. The
application-consistent `Backup Ollama` action stops only Ollama and archives that complete
storage tree to PBS as one `data.pxar`; this includes model files and the configuration
needed to use them. The GPU is hardware and is not in the archive.

`Restore Ollama` is plan-first. With an explicit recovery point and `overwrite=true`, it
stops the service, extracts and validates the archive in private staging, replaces only
Ollama's model/configuration tree, starts the service, and waits for `/api/tags`. A failed
restore leaves the service stopped for inspection. Restore or redeploy the VM first, then
rebind the same declared PCI device before restoring application data.

## GPU detach and rebind

Remove App stops the Compose service before calling the dedicated PCI detach seam. Detach
removes `hostpciN` only when both its PCI content and the guest's `_.dev+<slug>` provenance
tag prove that homelab-infra created the binding. A matching untagged operator assignment is
reported and left in place. The seam also restores a VM that was running before a failed
stop/remove/start sequence.

After a VM restore or move, verify the PCI address on the target node, use the recorded
console recovery path if the guest does not boot, re-run Deploy Ollama, and verify both
`nvidia-smi` and the Ollama `/api/tags` endpoint.
