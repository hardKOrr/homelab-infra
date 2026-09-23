# ComfyUI recovery and GPU authority

ComfyUI is a single Docker service on its own Debian VM. The VM is not the shared `ai`
LXC because its exact PCI device is dedicated to this guest. The instance configuration
must name the PCI address, and `config/proxmox.yml` must declare that same identifier under
one `mode: dedicated` device entry before deployment. The attach seam refuses a shared or
ambiguous declaration and refuses a device already assigned to another VM.

Before live binding, record:

- the ComfyUI instance, Proxmox node, VM name/VMID, and AI-stack purpose;
- the exact PCI address and the guest's GPU/console recovery procedure; and
- the VM model-storage path and its intended capacity.

## Model and configuration recovery

The ComfyUI role keeps models, user configuration, custom nodes, inputs, and outputs below
`app.storage_path`. `app.models_path` is the large persistent mount used for checkpoints and
LoRAs. The application-consistent `Backup ComfyUI` action stops only ComfyUI and archives
that entire storage tree to PBS as one `data.pxar`; this includes both model files and the
configuration needed to use them. The GPU is hardware and is not in the archive.

`Restore ComfyUI` is plan-first. With an explicit recovery point and `overwrite=true`, it
stops the service, extracts and validates the archive in private staging, replaces only the
ComfyUI storage tree, starts the service, and waits for `/system_stats`. A failed restore
leaves the service stopped for inspection. Restore or redeploy the VM first, then rebind the
same declared PCI device before restoring application data.

## GPU detach and rebind

Remove App stops the Compose service before calling the dedicated PCI detach seam. Detach
removes `hostpciN` only when both its PCI content and the guest's `_.dev+<slug>` provenance
tag prove that homelab-infra created the binding. A matching untagged operator assignment is
reported and left in place. The seam also restores a VM that was running before a failed
stop/remove/start sequence.

After a VM restore or move, verify the PCI address on the target node, use the recorded
console recovery path if the guest does not boot, re-run Deploy ComfyUI, and verify both
`nvidia-smi` and the ComfyUI system-stats endpoint.
