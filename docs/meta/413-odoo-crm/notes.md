# Notes — 413 Odoo CRM

## 2026-08-20 — Why this option exists

Odoo is the broadest option in the CRM group. Odoo Community includes CRM inside an integrated
business-application suite that can also cover sales, inventory, projects and other workflows.
That breadth is its differentiator and its main scope risk: the platform deploys a stable CRM
baseline, while the operator chooses any additional modules.

Upstream supports on-premise deployment and provides a Docker image. Odoo uses PostgreSQL and
maintains a filestore outside the database. This repository's backend rule still applies: the
PostgreSQL instance is deployed and recovered independently through the Batch B provisioning
contract, while the CRM backup coordinates database state with the matching filestore.

Community and Enterprise do not have the same feature set or license. The first implementation
must verify that the requested CRM workflow is present in Community and must state any material
limit. It must not make a paid Enterprise subscription an undeclared runtime dependency.
