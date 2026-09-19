#!/usr/bin/env sh
# Writes wrangler.deploy.toml = wrangler.toml + the KV binding.
# The namespace id is read from the environment so it never lands in the public repo.
#   CF_KV_NAMESPACE_ID=xxxx ./build_config.sh && npx wrangler deploy -c wrangler.deploy.toml
set -eu
cd "$(dirname "$0")"
: "${CF_KV_NAMESPACE_ID:?set CF_KV_NAMESPACE_ID (the id of your KV namespace)}"
cp wrangler.toml wrangler.deploy.toml
cat >> wrangler.deploy.toml <<TOML

[[kv_namespaces]]
binding = "VOCAB_KV"
id = "${CF_KV_NAMESPACE_ID}"
TOML
echo "wrote $(pwd)/wrangler.deploy.toml"
