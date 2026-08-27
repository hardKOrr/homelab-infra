# Application catalog

`applications.yml` is the canonical classification of applications that the platform can
deploy now. It answers how an operator finds an application: broad human purpose, then
application type, then name.

The catalog does not contain execution facts. Hosting kind, stack, ports, images, versions,
resource sizing, routes, and dependencies stay with the Ansible implementation that owns
them. A Docker application and a Kubernetes application with the same purpose belong beside
each other here.

Rundeck projects each entry to
`Applications/<category>/<type>/Deploy <name>`. `rundeck/render-job.py --check` rejects a
missing job, duplicate classification, mismatched name, or stale projected group. Platform
services and operator actions are classified separately in `rundeck/job-groups.yml`.

Add an entry when the application's playbook and Rundeck job become selectable. Future intent
belongs in `docs/meta/`; it is not runtime catalog state.
