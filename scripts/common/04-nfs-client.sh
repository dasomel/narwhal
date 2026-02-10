#!/bin/bash
set -euo pipefail

echo "=== NFS Client Installation ==="

# Install NFS client
sudo apt-get update
sudo apt-get install -y nfs-common

echo "=== NFS Client Installation Done ==="
