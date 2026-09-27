# Project documentation

Durable design and implementation contracts for this repository. Current work, its
specification, and its acceptance evidence live in
[GitHub Issues](https://github.com/hardKOrr/homelab-infra/issues), not here.

## Find the right document

| When you need to… | Read |
| --- | --- |
| Understand how the components fit together | [Architecture](architecture.md) |
| Write or review Ansible in this repository's dialect | [Specifications](specs/README.md), starting with [framework](specs/framework.md) |
| Know what a configuration key does, its schema, or merge order | [`../ansible/vars/CONTRACT.md`](../ansible/vars/CONTRACT.md) |
| Change or add an application | [`../ansible/playbooks/apps/README.md`](../ansible/playbooks/apps/README.md), then the role's `README.md` if it has one |
| Change a product's backup or restore behaviour | [Recovery acceptance](recovery-acceptance.md) and that product's recovery spec in [`specs/`](specs/README.md) |
| Add a domain estate | [Estate onboarding runbook](estate-onboarding.md) |
| Run a Rundeck job on the live lab and record evidence | [Live-lab evidence](live-lab.md) |
| Write a lab address, domain, node, or VMID in public text | [Lab placeholders](lab-placeholders.md) |
| Avoid a failure the gates cannot see | [Lessons and standing facts](lessons.md) |
| Run the lint and test gates | [`../gate/README.md`](../gate/README.md) |
| Change a Rundeck job or the runner bootstrap | [`../rundeck/README.md`](../rundeck/README.md) |
| File an issue or open a pull request | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |

[`../AGENTS.md`](../AGENTS.md) holds the concise operating rules for agents. Where a
specification and `CONTRACT.md` disagree, `CONTRACT.md` wins.

The retired `docs/meta/` slice records are preserved as history at
[commit `056d836`](https://github.com/hardKOrr/homelab-infra/tree/056d836/docs/meta). They
are not current contracts; verify anything taken from them against the code.
