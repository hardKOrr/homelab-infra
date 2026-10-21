# Ollama role

Deploy selects the VM carrying the instance tag. If that tag is missing, it can adopt one
`_+lab` VM whose Proxmox name exactly matches the instance, then records the instance tag
before running the application role. Multiple same-name owned VMs stop Deploy before it can
create another guest.

Ollama runs as one Docker service on its own Debian VM. Leave `app.gpu_device` empty for a
CPU deployment. When it names a device, the play assigns that PCI device exclusively with
`attach-pci-passthrough.yml`; the role then requires the GPU and Docker NVIDIA runtime.

`app.models_path` is the large model-storage mount exposed at `/root/.ollama` in the
container. It contains downloaded models and Ollama metadata and is covered by the
application-consistent PBS archive. The GPU is hardware and must be rebound after VM
recovery in GPU mode; it is not part of the archive.

For GPU deployments, declare the exact PCI device and keep node console recovery access.
Both CPU and GPU deployments need a persistent model-storage path. If PCI detach fails,
the seam restarts a previously running VM. Untagged assignments require operator recovery
and are not adopted.
