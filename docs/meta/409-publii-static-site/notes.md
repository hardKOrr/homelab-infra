# Notes — 409 Publii static-site hosting

## 2026-08-20 — Why this option leads

Publii is the lowest-operations path for the intended editor. It provides block, WYSIWYG and
Markdown editors on Windows, macOS and Linux, then publishes generated HTML. Claude can help
customize its Handlebars themes without making code the normal authoring path.

The deployment unit is the generated site, not Publii. The role must therefore be named and
designed as a generic static-site host. Publii is the first accepted publication client, not a
server dependency.

The upload must be atomic. Uploading directly into the live document root can leave a partially
updated public site after a failed sync. Publish into a staging directory, validate that an
entry page exists, then exchange the live tree while retaining one rollback generation.
