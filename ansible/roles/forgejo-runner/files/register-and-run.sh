#!/bin/sh
set -eu

config=/data/runner-config.yml
marker=/data/.homelab-infra-registered

if [ ! -s "$config" ]; then
  forgejo-runner generate-config > "$config"
fi

# The marker is written only after Forgejo accepts the token. On a rerun the existing
# runner configuration is reused and daemon reconnect is the only operation; this avoids
# creating duplicate runners while still recovering a disconnected daemon.
if [ ! -f "$marker" ]; then
  forgejo-runner register \
    --no-interactive \
    --instance "$FORGEJO_INSTANCE_URL" \
    --token "$FORGEJO_RUNNER_TOKEN" \
    --name "$FORGEJO_RUNNER_NAME" \
    --labels "$FORGEJO_RUNNER_LABELS" \
    --config "$config"
  touch "$marker"
fi

exec forgejo-runner daemon --config "$config"
