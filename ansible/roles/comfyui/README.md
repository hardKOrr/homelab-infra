# ComfyUI role

ComfyUI runs as one Docker service on its own Debian VM. The named PCI device is
assigned exclusively with `attach-pci-passthrough.yml`, and the role refuses to start a CPU-only deployment when the
guest GPU or Docker NVIDIA runtime is missing.

`app.models_path` is the large model-storage mount exposed to the container. The role also
keeps user configuration, custom nodes, inputs, and outputs under `app.storage_path`.
The application-consistent PBS archive covers that whole storage tree, including downloaded
checkpoints and LoRAs. The GPU is hardware and must be rebound after VM recovery; it is not
part of the archive.

Declare the exact PCI device and model-storage path; keep node console recovery access.
If PCI detach fails, the seam restarts a previously running VM. Untagged assignments
require operator recovery and are not adopted.
