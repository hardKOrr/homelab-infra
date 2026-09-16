# 410 — WordPress website hosting

**Status:** built
**Subject:** wife-friendly website hosting
**Related:** 408 (app catalog and MariaDB provisioning), 300 (Caddy wiring), 406 (PBS backup)

## Goal

Deploy an instanced WordPress site with a maintained block theme and the Site Editor as the
normal authoring surface. The app uses a separately deployed MariaDB instance through the
Batch B provisioning contract, stores generated administrator and database credentials in
Vaultwarden, and receives the platform's normal public route, monitoring, backup, removal and
restore behavior. This is the general-purpose option when the site needs forms, plugins,
commerce or broad theme choice.

## Remaining

- [x] Complete the role, app defaults, deploy playbook, documented config example and one-click
      Rundeck job with the backend instance named in `config/apps/<instance>.yml`.
- [x] Complete unattended first boot, create the initial administrator without printing its
      credential, store it in the canonical Vaultwarden item and make re-runs preserve it.
- [x] Install the maintained Twenty Twenty-Five block theme as the usable default; optional
      plugins and site content remain under operator control after first boot.
- [x] Wire the public route and monitor, and implement application-consistent backup and restore
      across wp-content/uploads plus the separately managed MariaDB database.
- [x] Add synthetic verification for visual-editor-ready theme setup, media persistence,
      idempotent deployment, image update/redeploy, removal, and cross-instance restore.
- [x] Pass both repository gates.
- [ ] Live evidence is deferred to the existing #35 isolated real-Proxmox acceptance lane.
      Before execution, name and authorize the exact target: instance `wordpress-acceptance`,
      services-stack host `stack-services-acceptance`, MariaDB instance
      `mariadb-wordpress-acceptance`, and the DNS name
      `wordpress-acceptance.<authorized-lab-domain>`. Use the named test site
      `WordPress acceptance site` only. On that isolated target, deploy the backend and app,
      confirm the public route and Uptime Kuma monitor, open the Site Editor, publish a page,
      upload and display a media item, rerun the deploy, perform an authorized image/core
      update, create a second target instance, restore the first target's PBS snapshot into
      it, verify its URL and administrator continuity, then remove only the named app resources
      and confirm the MariaDB backend and other services remain. This is deferred evidence, not
      a production cutover or cleanup request.

## Links

- [WordPress Site Editor](https://wordpress.org/documentation/article/site-editor/) — visual
  whole-site editing with a block theme
- `ansible/vars/app-defaults/wordpress.yml` — services stack, named MariaDB, public route,
  block theme and recovery defaults
- `ansible/roles/wordpress/` — official Docker image, WP-CLI bootstrap and paired recovery
- `ansible/playbooks/apps/wordpress.yml` — database provisioning, Docker and provider wiring
- `config.example/apps/wordpress.example.yml` — non-secret instance example
- `catalog/applications.yml`, `catalog/recovery.yml`, `rundeck/jobs/deploy-wordpress.yaml` —
  operator surfaces
- `gate/test-wordpress-contract.sh` — synthetic contract and recovery-boundary checks
