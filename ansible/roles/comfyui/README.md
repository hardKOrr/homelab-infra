# ComfyUI role

Deploy finds its VM through
[instance guest reuse](../../tasks/proxmox/README.md#instance-guest-reuse); PCI assignment
and detach follow [device passthrough](../../tasks/proxmox/README.md#device-passthrough).

ComfyUI runs as one Docker service on its own Debian VM. `app.accelerator` selects `cuda`
or `cpu` and defaults to `cuda` for existing configurations. CUDA mode requires the named
PCI device to be assigned exclusively with `attach-pci-passthrough.yml` and verifies that
the guest and Docker expose NVIDIA support. CPU mode uses a CPU image and floating-VM
placement, skips NVIDIA checks, and omits GPU requests from Compose.

Select the matching AI-Dock image explicitly. CUDA configurations keep the existing
`latest-cuda` default. For CPU, use the tested `cpu-22.04` image pinned to digest
`sha256:5f08ab173b4d78babb6b003a875d495a64378815237e8b80c9248923dcda9a52`. The older
`v2-cpu-22.04-v0.2.x` images have an unusable system Python and cannot start their
supervisor. Leave `app.gpu_device` empty in a CPU-only configuration.

AI-Dock protects the ComfyUI endpoint with its login service. The deploy health probe
does not follow that login redirect; it accepts the redirect as an available auth gate,
while the recovery drill verifies the actual login and API. A first CPU start may take
longer to initialize, so it gets a ten-minute health window.

`app.models_path` is the large model-storage mount exposed to the container. AI-Dock's
ComfyUI process stores user settings and workflows under `/opt/ComfyUI/user`; the role
mounts `app.config_path` there so those files are covered by recovery. On the first deploy
after this mount change, existing files are copied from the container and merged without
replacing files already in persistent storage. Custom nodes, inputs, outputs, and models
also stay under `app.storage_path`, which the application-consistent PBS archive covers.
The GPU is hardware and must be rebound after VM recovery; it is not part of the archive.

For CUDA, declare the exact PCI device; both modes declare the model-storage path and keep
node console recovery access.
