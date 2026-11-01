# Gate toolchain

Bootstrap once on Debian/Ubuntu (adjust the package manager for another distro):

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/.venvs/homelab-ansible
~/.venvs/homelab-ansible/bin/pip install --upgrade pip
~/.venvs/homelab-ansible/bin/pip install -r gate/requirements-dev.txt
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml
```

Where Galaxy is unreachable, install each pin in `ansible/requirements.yml` with
`ansible-galaxy collection install --no-deps git+https://github.com/ansible-collections/<name>.git,<version>`
(`ansible.utils` tags carry a `v`), plus `community.library_inventory_filtering` 1.1.1,
which `community.docker` needs.

Add `~/.venvs/homelab-ansible/bin` to `PATH` so Python scripts called by the platform
can import the installed dependencies.

Run from the repository root:

```bash
bash gate/lint.sh
bash gate/test.sh
```

Both commands always check the applicable tree and accept and ignore arguments.
They close stdin and select a local inventory so no live Proxmox inventory is invoked.

Lint runs `ansible-lint` over `ansible/{playbooks,roles,tasks,vars}`, parses Jinja,
checks repository links and fixture secrets ([fixtures](fixtures/README.md)), enforces
GitHub workflow policy, and runs `check-structure.py`. That script enforces the structure
rules in [`AGENTS.md`](../AGENTS.md#keeping-the-repository-small) against the exceptions
recorded in `structure.yml`. An exception may not grow, and one that no longer applies must
be deleted. `python gate/check-structure.py --update` prints the current values.

Test syntax-checks every playbook, then runs every `gate/test-*.py` and `gate/test-*.sh`
suite. Both phases run in parallel and replay each item's diagnostics in order. Set
`GATE_JOBS=n` to choose the concurrency (default: the number of cores), and
`GATE_SHARD=k/n` to run only the k-th of n slices (shard 1 also syntax-checks); CI runs
three shards on separate runners. The suites exercise production logic, expressions and
templates; each suite's docstring (header comment for a shell suite) says what it checks.

When stdout is not a terminal and `CI` is unset (an LLM agent's tool call), the gate runs
quiet: a passing lint prints one summary line per check, a passing test run prints
`N of M ok`, and any failure prints its full log. `GATE_QUIET=0` forces the full output;
CI lists every playbook and suite.

On a Windows checkout accessed through WSL, wrap each command:

```bash
wsl bash -lc 'bash gate/lint.sh'
wsl bash -lc 'bash gate/test.sh'
```

The Gate workflow runs lint and test on pull requests targeting master and pushes to master.
