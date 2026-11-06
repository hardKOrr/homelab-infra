# Workstation lab tools

Commands an agent or operator runs from a workstation checkout against the green lab.
Private values come from `$HOMELAB_LAB_DIR` at run time. Each script's header explains its
options; `<script> --help` prints it.

| Need | Command |
| --- | --- |
| Run anything that takes over a minute (a playbook, a drill, a backup) | `tools/lab-start <name> -- <command...>` |
| Block until it ends, in one call, then see its exit code, error lines and log tail | `tools/lab-wait <name>`; exit 124 means call it again |
| A shell command inside the runner, or on a lab node | `tools/lab-exec '<cmd>'`, `tools/lab-exec --node '<cmd>'` |
| A shell command inside one `_+lab` guest (LXC directly, VM through lab-run) | `tools/guest-exec <name or vmid> '<cmd>'` |

All four take a script on stdin instead of a command string. Exit codes are the command's.

When a session needs a lab helper that is not here, add it here in the same PR rather
than writing a private copy under `/tmp`: the next session needs it too.
`gate/test-lab-tools.py` covers `lab-start` and `lab-wait` without a lab.
