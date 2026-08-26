# Project documentation

This directory contains durable project design and implementation contracts.

- [Architecture](architecture.md) maps the main modules, execution flows, and fragile seams.
- [Specifications](specs/) define the repository's Ansible dialect and review contracts.
- [`../ansible/README.md`](../ansible/README.md) routes implementation work to the relevant
  Ansible contract or subsystem guide.
- [`../config.example/README.md`](../config.example/README.md) explains the tracked examples
  and the user-owned runtime configuration boundary.
- [`../AGENTS.md`](../AGENTS.md) contains the concise project operating instructions used by
  agent tools.
- [`../gate/README.md`](../gate/README.md) documents the executable lint and test gate.

Current work state and historical implementation notes live in [`meta/`](meta/).
They are project history, not normative architecture or specification sources.
