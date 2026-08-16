# No target in this lab

Slices here are code-complete and gate-green, and their acceptance requires a system this
lab does not run. They are **not** stalled work and they do not belong in `INDEX.md` — a
queue row implies someone could pick it up, and nobody can.

| # | Slice | What it would take |
|---|---|---|
| 301 | Nginx wire/unwire | an Nginx Proxy Manager install. The lab runs Caddy. |
| 305 | Pihole wire/unwire | a Pihole. The lab runs OPNsense + Unbound. |
| 600 | Semaphore project.json | a Semaphore install. The lab runs Rundeck. The project supports both runners on purpose, so the code stays; only its acceptance is unreachable here. |

Move a slice back into the queue the day the target exists. Until then, treat these three
as shipped: they are the reason `routing.proxy: nginx` and `dns: pihole` are valid config
values, and that claim rests on the gates, not on a live run.
