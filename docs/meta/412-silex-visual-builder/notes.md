# Notes — 412 Silex visual website builder

## 2026-08-20 — Replaced Grav with a true WYSIWYG option

Grav was initially considered as the fourth option. It has a browser administration panel, but
its content model remains Markdown-oriented and is not the requested WYSIWYG site-building
experience. Silex replaces it because visual layout is the primary workflow.

Silex exports standard HTML and CSS and can be self-hosted with Docker. Its upstream project also
documents a Claude-compatible MCP endpoint. That endpoint controls an authoring tool and must not
share the anonymous public route used by generated sites.

The static output belongs to slice 409. Silex owns editable project state; each `static-site`
instance owns one published result. This boundary lets a published site stay available while the
builder is stopped, updated or restored.

Silex is younger and changes faster than WordPress, Ghost or Publii. Implementation must pin a
verified release and prove project export and restore before this option is called built.
