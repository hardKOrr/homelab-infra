# Project documentation

This directory contains durable project design and implementation contracts.

- [Architecture](architecture.md) maps the main modules, execution flows, and fragile seams.
- [Estate onboarding runbook](estate-onboarding.md) gives the ordered operator path for
  adding a domain estate and proving it before workloads move onto it.
- [Specifications](specs/) define the repository's Ansible dialect and review contracts.
- [Live-lab evidence](live-lab.md) explains the approved Rundeck API workflow for collecting
  acceptance evidence without bypassing repository automation.
- [`../ansible/README.md`](../ansible/README.md) routes implementation work to the relevant
  Ansible contract or subsystem guide.
- [`../config.example/README.md`](../config.example/README.md) explains the tracked examples
  and the user-owned runtime configuration boundary.
- [`../AGENTS.md`](../AGENTS.md) contains the concise project operating instructions used by
  agent tools.
- [`../gate/README.md`](../gate/README.md) documents the executable lint and test gate.
- [Recovery acceptance](recovery-acceptance.md) defines the two-destination fixture protocol and evidence boundary.
- [Lessons and standing facts](lessons.md) records failure shapes the gates cannot see and
  structural facts that live nowhere else.
- [Proxmox Datacenter Manager](pdm.md) documents the PDM appliance's deployment boundary and
  day-2 jobs.
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) defines the issue-to-PR lifecycle: GitHub
  Issues as the live work queue, issue and PR templates, and the AO worker lifecycle from
  intake to handoff.

Current work, its specification, and its acceptance evidence live in
[GitHub Issues](https://github.com/hardKOrr/homelab-infra/issues). The former `docs/meta/`
slice records are retired and preserved as history at
[commit `056d836`](https://github.com/hardKOrr/homelab-infra/tree/056d836/docs/meta).
