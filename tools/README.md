# Workstation lab tools

Commands an agent or operator runs from a workstation checkout against the green lab.
Private values come from `$HOMELAB_LAB_DIR` at run time. Each script's header explains its
options; `<script> --help` prints it.

| Need | Command |
| --- | --- |
| Run anything that takes over a minute (a playbook, a drill, a backup) | `tools/lab-start <name> -- <command...>` |
| Block until it ends, in one call, then see its exit code, error lines and log tail | `tools/lab-wait <name>`; exit 124 means call it again |
| Start a job and wait on it in the same call (waits on it instead if it's already running) | `tools/lab-run <name> -- <command...>`; on 124 continue with `tools/lab-wait <name>` |
| A shell command inside the runner, or on a lab node | `tools/lab-exec '<cmd>'`, `tools/lab-exec --node '<cmd>'` (any node), `--runner-node` (runner host) |
| A shell command inside one `_+lab` guest (LXC directly, VM through lab-run) | `tools/guest-exec <name or vmid> '<cmd>'` |
| Run the whole recovery drill on one instance, stopping at the first failure | `tools/lab-start drill-<i> -- tools/lab-drill <instance>`; `--plan` prints the steps |
| Where an AO issue stands: open tasks, latest handoff or blocked comment, PR checks and unresolved threads, local HEAD against both | `tools/ao-brief [issue]` (read-only) |
| Check an app in a browser: sign in, pages render, a flow works | `tools/lab-browser <instance> <url> ansible/roles/<app>/browser/<spec>.js` |

`lab-exec` and `guest-exec` take a script on stdin instead of a command string. Exit codes are the command's.

For a GPU migration, `lab-drill --gpu-device <declaration> <instance>` uses Configure App
after Remove to merge only `app.gpu_device`, then runs the fresh install and restore on
the selected stack. `--stop-after-restore` verifies the restored canaries and leaves them
in place at a planned handoff, before final Deploy and canary cleanup; it claims no full
drill pass. These options require a stateful app with a canary.

Browser checks are specs kept with the app's role (`ansible/roles/<app>/browser/`), so the
next session reruns them instead of clicking through again. When a change affects what a
user sees, add or extend the spec in the same PR. Explore by hand with `playwright-cli` on
the runner if you must, but finish with a spec. The spec format is at the top of
`ansible/scripts/browser-check.js`; `ansible/roles/mautic/browser/smoke.js` is an example.

When a session needs a lab helper that is not here, add it here in the same PR rather
than writing a private copy under `/tmp`: the next session needs it too.
`gate/test-lab-tools.py` covers `lab-start`, `lab-wait` and `lab-run` without a lab, and
`gate/test-ao-brief.py` covers `ao-brief` from fixture data.

`lab-jellyfin-transcode --ref <branch> <instance> <media-file>` requests forced H.264
playback and requires a produced HLS segment plus a new hardware-encoder log with
nonzero frames. Run it with `lab-start` and `lab-wait`. The source must already be
available inside Jellyfin; its temporary library is removed even on failure. The
provisioned administrator credential stays inside the Ansible/guest execution path.

`lab-guest-state.py` is lab-drill's read-only node probe for
`drill.verify_remove_isolation`. With `--destroy-guest`, it checks target absence and
compares the other owned guests' configuration and power state across Remove.
