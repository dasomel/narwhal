#!/bin/bash
# PLACEHOLDER, replace with real RTK
# Simple removal of level=info or level=debug lines.
# This dummy filter is intentionally incomplete and does not guarantee passing all gates.
grep -vE 'level=(info|debug)' || true
