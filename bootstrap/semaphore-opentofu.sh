#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOFU_DIR="$REPO_ROOT/opentofu/semaphore-homelab-infra-project"
ENV_FILE="$REPO_ROOT/.env"

# Ensure semaphore data directory exists and is writable by the 'semaphore' user/group.
# Uses sudo when needed.
SEMAPHORE_DATA_DIR="/var/lib/semaphore"
if [ ! -d "$SEMAPHORE_DATA_DIR" ]; then
  echo "Creating $SEMAPHORE_DATA_DIR"
  if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo mkdir -p "$SEMAPHORE_DATA_DIR"
    else
      mkdir -p "$SEMAPHORE_DATA_DIR"
    fi
  else
    mkdir -p "$SEMAPHORE_DATA_DIR"
  fi
fi

# Set ownership to semaphore:semaphore and grant rwx to owner/group
if id -u semaphore >/dev/null 2>&1; then
  echo "Setting ownership and permissions on $SEMAPHORE_DATA_DIR"
  if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo chown -R semaphore:semaphore "$SEMAPHORE_DATA_DIR"
      sudo chmod -R 770 "$SEMAPHORE_DATA_DIR"
    else
      chown -R semaphore:semaphore "$SEMAPHORE_DATA_DIR" 2>/dev/null || true
      chmod -R 770 "$SEMAPHORE_DATA_DIR" 2>/dev/null || true
    fi
  else
    chown -R semaphore:semaphore "$SEMAPHORE_DATA_DIR"
    chmod -R 770 "$SEMAPHORE_DATA_DIR"
  fi
else
  echo "Warning: user 'semaphore' not found; created directory but did not change ownership."
fi

if ! command -v tofu >/dev/null 2>&1; then
  echo "ERROR: OpenTofu (tofu) is not installed or not in PATH." >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "WARNING: $ENV_FILE not found; expecting required env vars to be set." >&2
fi

: "${SEMAPHORE_URL:?Set SEMAPHORE_URL in .env}"
: "${SEMAPHORE_TOKEN:?Set SEMAPHORE_TOKEN in .env}"

export TF_VAR_semaphore_url="$SEMAPHORE_URL"
echo "Using Semaphore URL: $SEMAPHORE_URL"
export TF_VAR_semaphore_admin_token="$SEMAPHORE_TOKEN"

pushd "$TOFU_DIR" >/dev/null
tofu init -input=false
tofu apply -auto-approve

echo ""
echo "Outputs (store these securely):"
echo "github_public_key:"
tofu output -raw github_public_key
popd >/dev/null
