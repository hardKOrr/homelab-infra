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

## Native

`hosting.method: native` with `proxmox.guest_type: lxc` or `vm` (`tasks/native.yml`) installs
the pinned `ollama-linux-amd64` release archive from Ollama's manual Linux install and runs
it under its own `ollama` unit, in CPU mode. `~ollama/.ollama` links to `app.models_path`, so
a recovery point from Docker or native restores to either. Backup and restore are the shared
PBS client and restore swap with the `native` runtime.

## Kubernetes

`hosting.method: kubernetes` runs one CPU-mode pod (`tasks/kubernetes.yml`,
`templates/manifest.yaml.j2`) with the model store on a default-StorageClass claim.
Backup is the shared CronJob and restore the shared Job (`tasks/restore-kubernetes.yml`).
A GPU is not available there: it would need a dedicated GPU passed to one k3s node VM plus
a device plugin.
