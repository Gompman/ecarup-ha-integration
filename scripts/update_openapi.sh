#!/usr/bin/env sh
set -eu
curl --fail --silent --show-error --location \
  https://public-api.ecarup.com/swagger/v1/swagger.json \
  --output assets/ecarup-openapi.json
python -m json.tool assets/ecarup-openapi.json >/dev/null
echo "Updated assets/ecarup-openapi.json"
