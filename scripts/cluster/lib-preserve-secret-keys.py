#!/usr/bin/env python3
"""Merge foreign keys from an existing Kubernetes Secret into a desired Secret."""

import argparse
import json
from pathlib import Path


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("desired", type=Path)
  parser.add_argument("existing", type=Path)
  parser.add_argument("output", type=Path)
  parser.add_argument("--owned-key", action="append", default=[])
  args = parser.parse_args()

  desired = json.loads(args.desired.read_text())
  existing = json.loads(args.existing.read_text())
  owned_keys = set(args.owned_key)
  desired_data = desired.setdefault("data", {})
  for key, value in existing.get("data", {}).items():
    if key not in desired_data and key not in owned_keys:
      desired_data[key] = value

  args.output.write_text(json.dumps(desired, separators=(",", ":")) + "\n")


if __name__ == "__main__":
  main()
