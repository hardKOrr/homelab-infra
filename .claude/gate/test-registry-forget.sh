#!/usr/bin/env bash
# Focused tests for ansible/scripts/registry-forget.py — the registry pruner used by
# Remove App. The cases that matter are the ones it must NOT touch: a pruner that takes
# a neighbouring service with it turns one removal into an outage of everything that
# service wired.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
forget="$repo/ansible/scripts/registry-forget.py"

fail() { echo "registry-forget test failed: $*" >&2; exit 1; }

# Run the pruner and compare the result with an expected JSON document, key order
# independent.
expect() {
  local want="$1" input="$2" got
  got="$(printf '%s' "$input" | python3 "$forget")" \
    || fail "input was refused but should have succeeded: $input"
  python3 - "$want" "$got" <<'PY' || fail "wrong result for: $input"
import json, sys
want, got = json.loads(sys.argv[1]), json.loads(sys.argv[2])
if want != got:
    print("expected %s\n     got %s" % (json.dumps(want, sort_keys=True),
                                        json.dumps(got, sort_keys=True)), file=sys.stderr)
    raise SystemExit(1)
PY
}

refuse() {
  local because="$1" input="$2" err rc
  set +e
  err="$(printf '%s' "$input" | python3 "$forget" 2>&1 >/dev/null)"
  rc=$?
  set -e
  [ "$rc" -ne 0 ] || fail "input should have been refused: $input"
  grep -qi -- "$because" <<<"$err" || fail "refusal did not mention '$because' (said: $err)"
}

# The live case this was written for: a removed media app leaves an entry that
# resolve-media-registry.yml still calls usable.
expect '{"domain":"lab.example.com","media":{"sonarr":{"app":"sonarr","host":"http://h:8989"}}}' \
  '{"registry":{"domain":"lab.example.com","media":{"sonarr":{"app":"sonarr","host":"http://h:8989"},"sabnzbd-foxglove":{"app":"sabnzbd","host":"http://h:8086"}}},"instance":"sabnzbd-foxglove"}'

# A role entry is removed when it names the instance, and kept when it does not — the
# registry claims the lab HAS that service, and after the removal it does not.
expect '{"domain":"lab.example.com"}' \
  '{"registry":{"domain":"lab.example.com","sso":{"instance":"authentik","host":"http://h:9000"}},"instance":"authentik"}'
expect '{"sso":{"instance":"authentik","host":"http://h:9000"}}' \
  '{"registry":{"sso":{"instance":"authentik","host":"http://h:9000"}},"instance":"authentik-foxglove"}'

# Estate scopes are pruned the same way, and an estate is dropped once nothing is left
# in it — an estate scope holding an empty media map still claims something.
expect '{"estates":{"foxglove":{"sso":{"instance":"authentik-foxglove"}}}}' \
  '{"registry":{"estates":{"foxglove":{"sso":{"instance":"authentik-foxglove"},"media":{"sabnzbd-foxglove":{"app":"sabnzbd","host":"http://h:8086"}}}}},"instance":"sabnzbd-foxglove"}'
expect '{}' \
  '{"registry":{"estates":{"foxglove":{"sso":{"instance":"authentik-foxglove"}}}},"instance":"authentik-foxglove"}'

# THE ONES THAT MUST NOT MOVE. One estate's removal never reaches another estate's
# entries, and a same-named key in a different scope is a different service.
expect '{"estates":{"personal":{"sso":{"instance":"authentik"}}}}' \
  '{"registry":{"estates":{"personal":{"sso":{"instance":"authentik"}},"foxglove":{"sso":{"instance":"authentik-foxglove"}}}},"instance":"authentik-foxglove"}'

# An instance that appears nowhere leaves the registry byte-identical.
expect '{"domain":"lab.example.com","media":{"sonarr":{"app":"sonarr"}},"sso":{"instance":"authentik"}}' \
  '{"registry":{"domain":"lab.example.com","media":{"sonarr":{"app":"sonarr"}},"sso":{"instance":"authentik"}},"instance":"never-deployed"}'

# Scalars and non-mapping values survive untouched — `domain` is a string and the
# pruner walks the same map it does.
expect '{"domain":"lab.example.com","stacks":["media_stack"]}' \
  '{"registry":{"domain":"lab.example.com","stacks":["media_stack"]},"instance":"sonarr"}'

# An empty registry is a valid input: removing an app before any bootstrap ran.
expect '{}' '{"registry":{},"instance":"sonarr"}'

# Malformed input is refused, never guessed at — a pruner that treats a broken registry
# as an empty one would write an empty facts.yml over a working lab.
refuse "instance must be a non-empty string" '{"registry":{}}'
refuse "instance must be a non-empty string" '{"registry":{},"instance":""}'
refuse "registry must be a mapping" '{"registry":[],"instance":"sonarr"}'
refuse "input must be a JSON object" '[]'

echo "registry-forget focused tests passed."
