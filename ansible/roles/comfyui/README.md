# ComfyUI role

ComfyUI runs as one Docker service on its own Debian VM. `app.accelerator` selects `cuda`
or `cpu` and defaults to `cuda` for existing configurations. CUDA mode requires the named
PCI device to be assigned exclusively with `attach-pci-passthrough.yml` and verifies that
the guest and Docker expose NVIDIA support. CPU mode uses a CPU image and floating-VM
placement, skips NVIDIA checks, and omits GPU requests from Compose.

Select the matching AI-Dock image explicitly. CUDA configurations keep the existing
`latest-cuda` default. For CPU, use the tested, pinned image tag
`v2-cpu-22.04-v0.2.2`; the `v0.2.7` tag failed to launch its supervisor in the CPU
deployment check. Leave `app.gpu_device` empty in a CPU-only configuration.

AI-Dock protects the ComfyUI endpoint with its login service. The deploy health probe
does not follow that login redirect; it accepts the redirect as an available auth gate,
while the recovery drill verifies the actual login and API. A first CPU start may take
longer to initialize, so it gets a ten-minute health window.

`app.models_path` is the large model-storage mount exposed to the container. The role also
keeps user configuration, custom nodes, inputs, and outputs under `app.storage_path`.
The application-consistent PBS archive covers that whole storage tree, including downloaded
checkpoints and LoRAs. The GPU is hardware and must be rebound after VM recovery; it is not
part of the archive.

For CUDA, declare the exact PCI device; both modes declare the model-storage path and keep
node console recovery access. If PCI detach fails, the seam restarts a previously running
VM. Untagged assignments require operator recovery and are not adopted.
