# Notes

## 2026-09-06 — scope and implementation

Issue #47 asked for the SMTP platform contract 408 flagged as open (2026-08-17 decision:
"SMTP is a platform contract; its shape is not yet decided"). Modeled the shape on
`notifications` rather than `dns`: mail is always an external relay with no in-lab
instance option and no per-record external resource to create or remove, so unlike
`dns`/`pihole`/`opnsense` there is no `tasks/wiring/<provider>.yml` + matching
`tasks/unwiring/<provider>.yml` pair. `ansible/tasks/mail/resolve-mail.yml` fills the
same role `notifications` fills by being read directly — one shared task an app includes
to get a resolved `wiring_mail` fact, rather than a provider-selected external-resource
wiring file.

Left `rundeck/bootstrap-rundeck.sh` untouched. It is a single ~1800-line interactive
script with no test harness exercising its prompt flow in this repository, and the
existing `LAB_DNS_API_KEY`/`CLOUDFLARE_API_TOKEN` prompts it already has are the only
evidence of what a correct addition looks like. The contract does not depend on it: an
operator can write `LAB_MAIL_PASSWORD` (or, post-cutover, `homelab-infra/mail`) by hand
today. Follow-up prompt wiring is the safer next PR.

Did not touch any application role or app-defaults file — the issue's own Exclusions rule
out an application-specific mail workflow in this issue, and no Batch C row currently
declares `mail`-consuming config to wire against.

The live send/rotation test in the Verification section needs a named provider account,
sender domain and test mailbox per the issue's own "Live-system authority" section; none
was supplied, so that evidence remains open.
