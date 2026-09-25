#!/usr/bin/env bash
# Build the prod image, push it to ECR and apply infra/ with Terraform. Settings come from .env.aws (gitignored):
# DOMAIN_NAME, DATABASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, JWT_SIGNING_KEY, STORAGE_ENCRYPTION_KEY,
# ALLOWED_USERS, CLOUDFLARE_API_TOKEN, and optionally PLUGIN_MARKETPLACE and BUDGET_EMAIL.
set -euo pipefail
umask 077 # the Terraform state holds secrets: keep new files private
cd "$(dirname "$0")/.."
[[ -f .env.aws ]] || { echo "Missing .env.aws: create it with the settings listed at the top of $0." >&2; exit 1; }
set -a
. ./.env.aws
set +a
required="DOMAIN_NAME DATABASE_URL GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET JWT_SIGNING_KEY STORAGE_ENCRYPTION_KEY"
for name in $required ALLOWED_USERS CLOUDFLARE_API_TOKEN; do
  [[ -n "${!name:-}" ]] || { echo "Set $name in .env.aws." >&2; exit 1; }
done
for name in $required ALLOWED_USERS PLUGIN_MARKETPLACE BUDGET_EMAIL; do
  export "TF_VAR_$(echo "$name" | tr '[:upper:]' '[:lower:]')=${!name:-}"
done
export CLOUDFLARE_API_TOKEN
tf() { terraform -chdir=infra "$@"; }

tf init -input=false >/dev/null
tf apply -target=aws_ecr_repository.app # first run only: the image needs somewhere to go; later a no-op
repo=$(tf output -raw repository_url)

aws ecr get-login-password | docker login --username AWS --password-stdin "${repo%%/*}"
docker build --platform linux/arm64 --provenance=false --target prod -t "$repo:latest" .
docker push "$repo:latest"
image=$(docker inspect --format '{{index .RepoDigests 0}}' "$repo:latest") # by digest, so a new image redeploys

tf apply -var "image_uri=$image"
