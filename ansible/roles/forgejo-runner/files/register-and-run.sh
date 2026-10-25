#!/bin/sh
set -eu

data_dir=${FORGEJO_RUNNER_DATA_DIR:-/data}
config=$data_dir/runner-config.yml
marker=$data_dir/.homelab-infra-registered
runner_file=$data_dir/.runner

mkdir -p "$data_dir"
cd "$data_dir"

if [ ! -s "$config" ]; then
  forgejo-runner generate-config > "$config"
fi

# The marker contains a digest of all registration inputs, never the token itself. The
# role clears the saved runner file when Forgejo no longer knows that registration.
digest() {
  printf '%s' "$1" | sha256sum | awk '{ print $1 }'
}
registration_fingerprint=$(
  printf '%s:%s:%s:%s' \
    "$(digest "$FORGEJO_INSTANCE_URL")" \
    "$(digest "$FORGEJO_RUNNER_TOKEN")" \
    "$(digest "$FORGEJO_RUNNER_NAME")" \
    "$(digest "$FORGEJO_RUNNER_LABELS")" \
    | sha256sum \
    | awk '{ print $1 }'
)
expected_marker="v1:$registration_fingerprint"
registered_marker=
if [ -f "$marker" ]; then
  registered_marker=$(cat "$marker")
fi

# The register command creates a new server-side identity whenever it runs. Reuse the
# saved identity only while its inputs still match and its .runner file is present.
if [ "$registered_marker" != "$expected_marker" ] || [ ! -s "$runner_file" ]; then
  forgejo-runner register \
    --no-interactive \
    --instance "$FORGEJO_INSTANCE_URL" \
    --token "$FORGEJO_RUNNER_TOKEN" \
    --name "$FORGEJO_RUNNER_NAME" \
    --labels "$FORGEJO_RUNNER_LABELS" \
    --config "$config"

  marker_tmp=$marker.tmp.$$
  umask 077
  printf '%s\n' "$expected_marker" > "$marker_tmp"
  mv -f "$marker_tmp" "$marker"
fi

exec forgejo-runner daemon --config "$config"
