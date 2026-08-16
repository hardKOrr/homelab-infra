# 502 — notes

## 2026-07-25 — implementation

`playbooks/stacks/rollback-container.yml` implemented as three plays (locate host →
rewrite + recreate → notify). Both gates green; **this slice is what turned
`gate/test.sh` green** — it was the only playbook failing syntax-check.

### Deviations from the README approach

**Service selection, not just the container name.** The README assumed one image line
per project. Multi-service projects exist in this repo (Authentik ships app + worker +
postgres + redis), so the play parses the Compose file with `from_yaml` and rolls back
the service named `<container>`, falling back to the project's first service. It edits
that service's exact image reference with `replace`, so a sibling service's image is
never touched.

**Tag splitting is done on the last path segment.** `registry.lan:5000/app` must not be
read as repo `registry.lan` tag `5000/app`. Digest-pinned references (`repo@sha256:…`)
are out of scope and documented as such in the playbook header.

**`recreate: always` only when the tag actually moved.** The README asked for
unconditional `recreate: always`, which would bounce a healthy container on a re-run
with the same target tag. Acceptance item 4 (idempotent re-run) is what forced this:
the play now recreates only when the rewrite reported changed.

**Freeze-without-a-tag is honest about `:latest`.** The README's "if image_tag is
omitted, pin to the currently running tag" is a no-op when the project is on a floating
tag — re-pinning `:latest` freezes nothing. Rather than pretend, the play reports it in
both the console and the Ntfy message and tells the operator to pass `-e image_tag=`.
Resolving the running container's real version would mean digest pinning, which is a
different (and much less legible) rollback story.

**Health check is opt-in via the instance config.** `config/apps/<container>.yml` is
loaded optionally for `app.port`; with no port recorded, the probe is skipped rather
than guessed. A failed probe reports and does not fail the job — the rollback already
happened and the operator needs to see that.

### What live acceptance must confirm

- A real Watchtower-broken container rolls back and comes up on the older tag.
- `docker_compose_v2_pull` with `services: [<name>]` behaves on a multi-service project.
- The Ntfy message carries both refs (it reads them back from Play 2 hostvars, which is
  the part that only proves out in a real run).
