# 2026-09-15 — repository fixture evidence

- Fixture case: `open-webui:native:application`; tracked image label: `main` (mutable tag, not an installed-version observation).
- Synthetic A locator: `fixture/native/open-webui/A`; independent B locator: `fixture/native/open-webui/B`.
- New destination: passed with the source unreachable, target identity isolated, application state/key boundary asserted, and separately owned upstream model data excluded.
- Existing destination: A → B → restore A passed; B-only chat/upload state disappeared, retained B recovered an interrupted target, and retry A passed.
- Negative fixture matrix: wrong target, wrong storage/identity, missing key, incompatible version, incomplete/corrupt point, excluded external data and interrupted replacement all refused or remained visibly incomplete.
- Evidence output carries no backup contents, credential values, endpoint values or live configuration. `live_verified` remains `false`; schedule firing, installed version/digest, PBS identity/age/integrity and live application results are deferred to the former #80 acceptance roll-up (now closed).
- Verification: `bash gate/lint.sh` and `bash gate/test.sh` passed; focused Open WebUI tests passed.
