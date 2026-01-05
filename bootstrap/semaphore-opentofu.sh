#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOFU_DIR="$REPO_ROOT/opentofu/semaphore-homelab-infra-project"
ENV_FILE="$REPO_ROOT/.env"

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
