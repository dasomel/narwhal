#!/usr/bin/env bash
set -euo pipefail

# Offline contract gate: a committed portal-compatible event must validate, and a
# copied event with an invalid fixed schema version must be rejected. The mutation
# proves this check is an executable guard, not merely a successful example run.

cd "$(dirname "$0")/../.."

sample="examples/event-envelope.operation.started.json"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

python3 scripts/narwhalctl.py events emit --file "${sample}"
echo "PASS: canonical event envelope sample validates"

invalid_event="${tmp_dir}/invalid-event.json"
cp "${sample}" "${invalid_event}"
python3 - "${invalid_event}" <<'PYEOF'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    event = json.load(handle)
event["schema_version"] = "9.9"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(event, handle)
PYEOF

if python3 scripts/narwhalctl.py events emit --file "${invalid_event}" >/dev/null 2>&1; then
  echo "FAIL: narwhalctl accepted an event with an unsupported schema_version" >&2
  exit 1
fi
echo "PASS: unsupported schema_version is rejected"
