# Implementation specifications

These specifications define the reviewable contracts for Ansible code in this repository.
They supplement [`AGENTS.md`](../../AGENTS.md), which remains the concise project instruction
source. [`../architecture.md`](../architecture.md) maps the components that implement them.

| Specification | Contract |
|---|---|
| [Framework](framework.md) | Toolchain, layout, lint, tests, and review conventions |
| [Configuration layering](config-layering.md) | Defaults, user overrides, and module-argument boundaries |
| [Namespace merge discipline](namespace-merge-discipline.md) | Non-destructive writes to shared dictionaries |
| [Jinja type discipline](jinja-type-discipline.md) | Explicit conversion and safe dictionary access in templates |
| [One-click, idempotent, notified](one-click-idempotent.md) | Product-level playbook behavior |
| [Provider no-op wiring](provider-noop-wiring.md) | Symmetric and optional platform integration |
| [Secrets handling](secrets-handling.md) | Seed mode, Vault mode, redaction, and secret storage |
| [Device passthrough](device-passthrough.md) | Shared LXC device binds and dedicated VM PCIe/USB passthrough |

When a specification and the authoritative variable schema differ, update the specification to
match [`ansible/vars/CONTRACT.md`](../../ansible/vars/CONTRACT.md). Work state and historical
decisions belong in [`../meta/`](../meta/), not in these normative documents.
