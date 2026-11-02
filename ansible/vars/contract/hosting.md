# App hosting contract

Every app defaults block declares its current hosting form and the combinations its deploy
supports. An instance may select only `hosting.method`; the supported and impossible lists
remain app-owned declarations and cannot be overridden in `config/apps/<instance>.yml`.
Deploy resolves one of `docker-lxc`, `docker-vm`, `kubernetes`, `native-lxc`, or
`native-vm` before any task that changes state, then refuses a combination absent from the
app's `hosting.supported`. If the combination is in `hosting.impossible`, the failure also
shows the app's technical reason.

```yaml
example_defaults:
  hosting:
    method: docker             # docker | kubernetes | native
    supported: [docker-vm]     # current default form; expand only with an implemented path
    impossible: {}             # combination: technical reason, when truly impossible
```

For a stack-backed Docker app, guest type comes from the effective stack's
`stack_defaults.<stack>.guest_type`; `stack_defaults._default.guest_type` is `vm`, and
`infrastructure.stacks.<stack>.guest_type` overrides it for that runner. A standalone
Docker app keeps the VM shape in its app defaults. A native app uses the merged
`proxmox.guest_type` when present, then the app's existing `proxmox.type` or guest block.
Kubernetes is the cluster form and has no guest suffix.

Appliance VMs and the k3s cluster use `native-vm`. Each lists the other four combinations
in `hosting.impossible` with a reason; other apps leave that mapping empty until a
technical impossibility is established. Unsupported but possible forms stay out of
`hosting.supported` without inventing a `todo` status.
