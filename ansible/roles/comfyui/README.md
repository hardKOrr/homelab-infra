# ComfyUI role

ComfyUI runs as one Docker service on its own Debian VM. The VM is deliberately not the
shared `ai` LXC: the named PCI device is assigned exclusively with
`attach-pci-passthrough.yml`, and the role refuses to start a CPU-only deployment when the
guest GPU or Docker NVIDIA runtime is missing.

`app.models_path` is the large model-storage mount exposed to the container. The role also
keeps user configuration, custom nodes, inputs, and outputs under `app.storage_path`.
The application-consistent PBS archive covers that whole storage tree, including downloaded
checkpoints and LoRAs. The GPU is hardware and must be rebound after VM recovery; it is not
part of the archive.

Before live binding, record the ComfyUI instance, Proxmox node, VM/VMID, exact PCI address,
GPU console recovery path, and model-storage path. If a PCI detach fails, the passthrough
seam restores a VM that was running before the attempt; an untagged assignment is left for
operator recovery rather than adopted.
