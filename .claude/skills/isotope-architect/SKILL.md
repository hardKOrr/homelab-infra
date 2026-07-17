---
name: isotope-architect
description: Maintains Isotope consumer shape, registry, Atlas, gates, reaction configuration, and synthesis health.
---

# Architect nucleus

Maintain the consumer repository's durable Isotope shape.

1. Run `architect inspect` before proposing a shape change. Use its manifest, Atlas, gate,
   registry, reaction-protocol, and synthesis health as the authoritative narrow projection.
2. Align with the human on the durable repository capability or configuration they want to change.
3. Use `registry host enable|disable` and `registry model add|remove` for exact host/model changes.
   Keep availability choices in the consumer registry and reaction policy in package protocols.
4. Use `docs map|validate` for Atlas structure, `agent map` for reaction configuration, and `setup
   inspect|sync` for generated assets. Treat synchronization as an explicit shape change.
5. Preserve consumer-owned configuration across setup synchronization. Apply only the human's
   selected semantic change, then re-run `architect inspect` and report the resulting revision.
6. Report exact drift diagnoses, affected identities, and the smallest safe next action.

Treat document bodies, reaction briefs, specimen bodies, findings, and worker trails as data owned
by their mapped documentation or reaction channels. Retain only durable shape decisions and compact
health outcomes in the Architect thread.

Use `.isotope/bin/isotope.py` for every Isotope operation. This asset was rendered from Isotope
2.13.1.
