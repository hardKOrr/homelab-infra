# ComfyUI role

ComfyUI runs as one Docker service on its own Debian VM. `app.accelerator` selects `cuda`
or `cpu` and defaults to `cuda` for existing configurations. CUDA mode requires the named
PCI device to be assigned exclusively with `attach-pci-passthrough.yml` and verifies that
the guest and Docker expose NVIDIA support. CPU mode uses a CPU image and floating-VM
placement, skips NVIDIA checks, and omits GPU requests from Compose.

Select the matching AI-Dock image explicitly. CUDA configurations keep the existing
`latest-cuda` default. For CPU, use a published CPU version tag such as
`v2-cpu-22.04-v0.2.7`; the unpinned `latest-cpu` tag is not in AI-Dock's current tag
scheme. Leave `app.gpu_device` empty in a CPU-only configuration.

`app.models_path` is the large model-storage mount exposed to the container. The role also
keeps user configuration, custom nodes, inputs, and outputs under `app.storage_path`.
The application-consistent PBS archive covers that whole storage tree, including downloaded
checkpoints and LoRAs. The GPU is hardware and must be rebound after VM recovery; it is not
part of the archive.

For CUDA, declare the exact PCI device; both modes declare the model-storage path and keep
node console recovery access. If PCI detach fails, the seam restarts a previously running
VM. Untagged assignments require operator recovery and are not adopted.
