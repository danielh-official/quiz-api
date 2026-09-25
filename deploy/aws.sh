#!/usr/bin/env bash
# Build and deploy to AWS Lambda (template.yaml). Settings come from .env.aws, which is gitignored:
# DOMAIN_NAME, DATABASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, JWT_SIGNING_KEY, STORAGE_ENCRYPTION_KEY,
# ALLOWED_USERS and optionally PLUGIN_MARKETPLACE.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a && . ./.env.aws && set +a

sam build
sam deploy --parameter-overrides \
  "ParameterKey=DomainName,ParameterValue=$DOMAIN_NAME" \
  "ParameterKey=DatabaseUrl,ParameterValue=$DATABASE_URL" \
  "ParameterKey=GithubClientId,ParameterValue=$GITHUB_CLIENT_ID" \
  "ParameterKey=GithubClientSecret,ParameterValue=$GITHUB_CLIENT_SECRET" \
  "ParameterKey=JwtSigningKey,ParameterValue=$JWT_SIGNING_KEY" \
  "ParameterKey=StorageEncryptionKey,ParameterValue=$STORAGE_ENCRYPTION_KEY" \
  "ParameterKey=AllowedUsers,ParameterValue=$ALLOWED_USERS" \
  "ParameterKey=PluginMarketplace,ParameterValue=${PLUGIN_MARKETPLACE:-}"
