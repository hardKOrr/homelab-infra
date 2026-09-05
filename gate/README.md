# Gate toolchain

Run repository lint and test checks through the committed wrappers. They close stdin, select
the correct change scope, and prevent shell-relay quoting from silently turning a check into a
no-op.

Invoked as:

```
lint:      bash gate/lint.sh
test:      bash gate/test.sh
container: bash gate/container.sh
kind:      bash gate/kind.sh
```

Append `--all` to `lint.sh` or `test.sh` to force the full sweep (see *Scope* below).
`container.sh` and `kind.sh` have no scope narrowing — each always runs its one scenario.

On a Windows checkout accessed through WSL, prefix each command with `wsl bash -lc '...'`, e.g.
`wsl bash -lc 'bash gate/lint.sh'`. A native Linux checkout needs no such prefix.

- `lint.sh` — `ansible-lint` profile `min` over `playbooks roles tasks vars`.
- `check-links.py` — validates repository-local Markdown links and repo-root `docs/*.md`
  references in tracked and untracked text files.
- `check-output-anchors.py` — links operator-facing output to the Markdown passage it
  restates. See *Output anchors* below.
- `check-fixture-secrets.py` — rejects a tracked path under `config/` and a secret-shaped
  key under `gate/fixtures/` or `ansible/molecule/` whose value does not look like a
  reviewable placeholder. Reuses the exact key-shape regex
  `ansible/scripts/secret-shape.py` enforces on generated facts. See
  `docs/specs/secrets-handling.md`.
- `check-workflow-policy.py` — rejects a `.github/workflows/*.yml` job with no explicit
  `permissions`, a `secrets` reference in a `pull_request`-triggered job, or a
  self-hosted runner target with no `environment` approval gate. See
  `docs/specs/secrets-handling.md`.
- `test.sh` — `ansible-playbook --syntax-check` over every playbook, with the Proxmox dynamic
  inventory neutralized (`ANSIBLE_INVENTORY=localhost,`) so no credentials are needed.
- `lib-scope.sh` — sourced by both; decides full sweep vs. changed-only.
- `container.sh` — runs the Molecule container role integration harness at
  `ansible/molecule/docker-app` (converge, idempotence, verify, teardown) against a
  disposable Docker target. Syntax checking cannot show that rendered Compose
  configuration is valid or that a Docker-hosted role actually converges; this lane
  does. See `ansible/molecule/docker-app/README.md` for scope and rationale.
- `kind.sh` — runs the Kubernetes smoke-test lane at `gate/kind-app` (converge,
  idempotence, verify, teardown) against a disposable Kind cluster, proving the shared
  `tasks/kubernetes/*.yml` hosting-backend contract with FlareSolverr as the
  representative stateless application. See `gate/kind-app/README.md` for the full
  smoke matrix, scope, and rationale.

## Output anchors

Some text the platform prints to an operator restates a passage a `README.md` owns — the
bootstrap script's NETWORK and NEXT sections, the Get Config archive warning, the Reimport
Jobs description. A documentation pass can correct the contract and leave the printed text
stating the superseded thing, which `check-links.py` cannot see.

Declare the canonical passage in Markdown:

```
<!-- output-source:network-prerequisite sha=7cfe6710 -->
...the passage...
<!-- /output-source:network-prerequisite -->
```

Name the same id in a comment beside every place that restates it:

```
# Restates output-source:network-prerequisite, canonical in README.md.
```

`check-output-anchors.py` enforces the link, not textual equality — printed text
interpolates live values and wraps to a console. It fails when an anchor names no declared
passage, a passage has no consumer left, an id is declared twice, or a passage's content no
longer hashes to its declared `sha`.

The hash is the update chain. Change the canonical passage and the gate fails, naming every
output that restates it. Re-read each one, correct it, then record the new hash:

```
python3 gate/check-output-anchors.py --update
```

Use a new id when new output starts restating a documented passage. Do not run `--update`
to clear a failure you have not read.

## Scope

A full sweep starts a separate `ansible-playbook` process for every playbook and lints the whole
Ansible tree. Both gates therefore narrow by default to what the working tree changed. Ambiguous
scope falls back to the full sweep:

| Situation | Scope |
| --- | --- |
| `--all` argument, or `GATE_SCOPE=all` | full |
| Clean working tree | full |
| `git` unreadable (e.g. a checkout git refuses on dubious ownership) | full |
| `roles/ tasks/ vars/ inventory/ files/ ansible.cfg requirements.yml gate/` touched | full |
| Only Markdown touched, including Markdown inside an Ansible or gate directory | changed; skip Ansible lint and syntax checks |
| Only playbooks/docs touched | changed |

A clean tree resolving to *full* is the load-bearing rule: the run that gates a commit happens
after the commit, so there is no state in which "nothing changed" reports a green. A changed
playbook also drags in any playbook whose text names it, because `bootstrap.yml` chains the app
playbooks with `import_playbook`.

Markdown never promotes a changed run to full scope. `lint.sh` still compiles every Jinja
expression and runs `check-links.py`, and `test.sh` still runs every focused regression test.
The optimization skips only Ansible lint and playbook syntax checks that cannot consume a
Markdown file.

`test.sh` checks playbooks in parallel. Override its concurrency with `GATE_JOBS=n`.

Both export `ANSIBLE_CONFIG` to the checkout's absolute path derived from `$PWD`: on a checkout
Ansible's world-writable-cwd check flags (e.g. NTFS under `/mnt/c` on a WSL checkout), it
silently ignores a cwd-relative `ansible.cfg` otherwise.

## One-time bootstrap (fresh machine)

Interactive sudo, run once (Debian/Ubuntu; adjust the package manager for another
distro):

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/.venvs/homelab-ansible
~/.venvs/homelab-ansible/bin/pip install --upgrade pip
~/.venvs/homelab-ansible/bin/pip install -r gate/requirements-dev.txt
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml
```

Add the venv's `bin/` to `PATH` (shell profile, or per-run) so bare `python3` calls inside
the gate's own scripts — the ones that parse YAML or render Jinja outside the venv's own
binaries, e.g. `ansible/scripts/secret-shape.py`, `rundeck/render-job.py` — resolve to an
interpreter that has `pyyaml`/`jinja2` installed, instead of falling through to a system
Python that does not.

## Continuous integration

`.github/workflows/gate.yml` runs `bash gate/lint.sh --all` and `bash gate/test.sh --all`
as separate checks (`gate / lint`, `gate / test`) on every pull request targeting `master`.
It repeats the bootstrap above on a clean `ubuntu-latest` runner — same venv path
(`~/.venvs/homelab-ansible`), same requirements files, `PATH`-prepended the same way — so a
green run there means the bootstrap above still works from scratch. The workflow needs no
secrets and never touches `config/`: both gates already neutralize the Proxmox inventory
(`ANSIBLE_INVENTORY=localhost,`), and `--all` forces the full sweep regardless of the
runner's (always clean) working tree.

The same workflow also runs `gate / container` (`bash gate/container.sh`) and
`gate / kind` (`bash gate/kind.sh`) on every pull request. Both are fixture/cluster
evidence, not production live-lab acceptance: `container` converges a disposable Docker
target, `kind` converges a disposable single-node Kind cluster, and neither reaches a real
Proxmox endpoint, a real Kubernetes cluster, or any repository secret.
