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
| Check an app in a browser: sign in, pages render, a flow works | `tools/lab-browser <instance> <url> ansible/roles/<app>/browser/<spec>.js` |

`lab-exec` and `guest-exec` take a script on stdin instead of a command string. Exit codes are the command's.

Browser checks are specs kept with the app's role (`ansible/roles/<app>/browser/`), so the
next session reruns them instead of clicking through again. When a change affects what a
user sees, add or extend the spec in the same PR. Explore by hand with `playwright-cli` on
the runner if you must, but finish with a spec. The spec format is at the top of
`ansible/scripts/browser-check.js`; `ansible/roles/mautic/browser/smoke.js` is an example.

When a session needs a lab helper that is not here, add it here in the same PR rather
than writing a private copy under `/tmp`: the next session needs it too.
`gate/test-lab-tools.py` covers `lab-start` and `lab-wait` without a lab.
