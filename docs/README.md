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
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) defines the issue-to-PR lifecycle: GitHub
  Issues as the live work queue, issue and PR templates, and the AO worker lifecycle from
  intake to handoff.

Specifications, decision records, and acceptance evidence for issues that need more detail
than fits an issue body live in [`meta/`](meta/). They are not normative architecture or
specification sources on their own — the specs under [`specs/`](specs/) remain that.
