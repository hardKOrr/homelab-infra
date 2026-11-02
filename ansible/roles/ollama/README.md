# Ollama role

The VM is chosen by [instance guest reuse](../../tasks/proxmox/README.md#instance-guest-reuse),
and a GPU is assigned and released through
[device passthrough](../../tasks/proxmox/README.md#device-passthrough).

Ollama runs as one Docker service on its own Debian VM. Leave `app.gpu_device` empty for a
CPU deployment. When it names a device, the play assigns that PCI device exclusively with
`attach-pci-passthrough.yml`; the role then requires the GPU and Docker NVIDIA runtime.

`app.models_path` is the large model-storage mount exposed at `/root/.ollama` in the
container. It contains downloaded models and Ollama metadata and is covered by the
application-consistent PBS archive. The GPU is hardware and must be rebound after VM
recovery in GPU mode; it is not part of the archive.

For GPU deployments, declare the exact PCI device and keep node console recovery access.
Both CPU and GPU deployments need a persistent model-storage path.
