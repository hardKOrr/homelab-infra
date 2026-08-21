# Notes — 417 AcelleMail email marketing

## 2026-08-21 — Scope

The linked site calls the product AcelleMail; “Acelle Send” is the site and domain name. This
slice uses the product name so role, playbook and instance names have one stable spelling:
`acellemail`.

AcelleMail is commercial source-available software, not an OSI open-source project. The regular
license is sold for one end product/domain, and the purchased PHP/Laravel source is the deployment
artifact. This repository must automate a bundle the operator already has rather than fetch,
embed or redistribute the application. `/artifacts/` is already the ignored runner path for such
material; activation or purchase material is secret and belongs in Vaultwarden.

Upstream documents both native Linux and Docker production installs. Docker is the project
default for an application with this workload shape. The upstream Docker design is not a single
immutable image: it builds a PHP runtime, puts the purchased application tree in a persistent
code volume, and runs separate web, queue-worker and scheduler services. AcelleMail's patch flow
mutates that code volume and requires all PHP services to restart together. Implementation must
therefore prove update and rollback behavior instead of assuming Watchtower can update it like a
registry image.

The upstream Docker example includes MySQL and Redis inside its Compose project. This platform
does not hide backends inside an application deployment, so AcelleMail consumes separately
deployed Batch B instances. MariaDB is the proposed relational backend because AcelleMail supports
it and slice 408 already assigns that backend to mail-capable applications. Redis supports queue
processing and coordination; implementation must verify whether any Redis data is disposable
before defining its restore behavior.

AcelleMail is the marketing application, not the mail transport. It connects to SMTP or provider
APIs such as Amazon SES, SendGrid and Mailgun. A complete deployment also accounts for sender
authentication, return paths, bounces, complaints, throttling and reputation. The existing planned
SMTP contract is a starting point, but an SMTP login alone cannot satisfy the acceptance criteria.

Blanket Authentik `forward_auth` would break recipient-facing URLs and provider callbacks. Start
with `routing.identity: catalog`: AcelleMail owns administrator and user authentication, while the
platform may add a launch tile. Verify the exact public path set against the purchased release at
implementation time rather than encoding routes from current website copy.
