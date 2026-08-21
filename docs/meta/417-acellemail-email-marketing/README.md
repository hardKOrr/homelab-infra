# 417 — AcelleMail email marketing

**Status:** open
**Subject:** email marketing
**Related:** 408 (app catalog, MariaDB, Redis and outbound mail), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced AcelleMail service for mailing lists, visual campaign authoring, scheduled
sends, automations and delivery reporting. The service consumes separately deployed MariaDB and
Redis instances, runs its web application, queue workers and scheduler as distinct Docker
services, and stores generated credentials and license material in Vaultwarden. The operator
supplies the licensed source bundle through the ignored artifact path. AcelleMail uses the
platform's public route without blanket forward authentication because recipient tracking,
unsubscribe, signup and sending-provider callback endpoints must remain reachable.

## Remaining

- [ ] Complete slice 408's MariaDB and Redis provisioning contract before implementing this
      consumer; select versions supported by the AcelleMail release supplied by the operator
- [ ] Define the licensed-artifact contract: accept an operator-supplied AcelleMail source bundle
      from `/artifacts/`, keep purchase or activation material in Vaultwarden, and never commit or
      redistribute either
- [ ] Prove the upstream Docker production path in `services_stack`, including the mutable code
      volume required by AcelleMail's patch updater and coordinated restart after an update
- [ ] Add the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with the MariaDB and Redis instances named in `config/apps/<instance>.yml`
- [ ] Automate installation and administrator creation without the web wizard, preserve all
      generated secrets on re-run, and run web, queue-worker and scheduler health checks
- [ ] Scope and consume the platform outbound-mail contract as an AcelleMail sending server;
      support generic SMTP first and keep provider-specific API, bounce and complaint credentials
      explicit rather than treating successful SMTP authentication as complete delivery wiring
- [ ] Publish the application-owned login and required recipient endpoints, wire the Uptime Kuma
      monitor and document the SPF, DKIM, DMARC, return-path and provider-callback records or
      actions that cannot be created through the configured DNS provider
- [ ] Implement application-consistent backup and restore across the mutable AcelleMail files,
      MariaDB and Redis state; preserve subscriber consent, suppression, bounce and complaint data
- [ ] Prove list import, double opt-in, unsubscribe, one immediate campaign, one scheduled
      campaign, bounce/complaint processing, a second idempotent deploy, update, removal and
      cross-instance restore
- [ ] Pass both repository gates

## Links

- [AcelleMail product site](https://acellesend.com/) — product identity, licensing and features
- [AcelleMail Docker deployment guide](https://acellesend.com/kb/articles/docker-deployment-guide-for-acellemail) — app, worker, scheduler, persistence and update shape
- [AcelleMail sending documentation](https://acellesend.com/kb/category/sending-deliverability) — sending providers, throttling, bounces and complaints
- `docs/meta/408-app-catalog/README.md` — AcelleMail and its shared backend prerequisites
- notes.md — scope evidence and implementation notes
