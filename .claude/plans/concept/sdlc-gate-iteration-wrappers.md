# sdlc-gate-iteration-wrappers

Feedback to sdlc-architect (found while authoring `build.yml` `allow:` on 2026-07-06).

**Gap:** `allow:` is deliberately empty. The entire toolchain lives inside WSL behind
`wsl bash -lc '<inner string>'`, and that relay cannot be an allow prefix: guard-git's
simple-command check treats the single-quoted inner string as literal, so any chained inner
command (`wsl bash -lc 'ansible-lint x && anything'`) would auto-approve — the relay is a bare
interpreter in disguise. Today only the two exact gate strings ride through; every ad-hoc
iteration command (lint one file, syntax-check one playbook, `ansible-doc`, worked-example
localhost plays) prompts the human mid-run.

**Proposed fix, when a run actually stalls on this:** add argv-form wrappers under
`.claude/gate/` — invoked as `wsl bash .claude/gate/<x>.sh <path>` with NO inner shell string,
so arguments pass as argv and chaining characters stay unquoted on the Windows side, failing
guard-git's simple-command check instead of smuggling through. Candidates:

- `lint-file.sh <path…>` — `ansible-lint -c .ansible-lint <paths>` scoped to the named files
- `syntax-check.sh <playbook>` — one playbook instead of the full sweep

Each wrapper must replicate the env exports from `lint.sh`/`test.sh` (ANSIBLE_CONFIG absolute,
ANSIBLE_INVENTORY=localhost,), be forced to LF in `.gitattributes`, and be declared in
`build.yml` `allow:` as the full argv-form prefix (e.g. `wsl bash .claude/gate/lint-file.sh`).
Verify `bash` non-login resolves the venv path (`$HOME/.venvs/homelab-ansible/bin/…` is
absolute, so it should) before dropping `-l`.

Do not build this preemptively — it earns its place the first time a construction run stalls on
iteration prompts.
