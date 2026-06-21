#!/bin/bash
# Contract:
# - Run COMPRESSOR on raw log file.
# - R = grep -Ec "EVIDENCE_RE" raw
# - C = grep -Ec "EVIDENCE_RE" compressed
# - Fallback conditions:
#   1. COMPRESSOR exits non-zero
#   2. compressed output is empty
#   3. C < R (evidence lost)
# - If fallback: stdout = raw, stderr = RTK_FALLBACK=1, exit 0
# - If normal: stdout = compressed, stderr = RTK_FALLBACK=0, exit 0

set -euo pipefail

RAW_FILE="$1"
COMPRESSOR="${COMPRESSOR:-./compressor/dummy-filter.sh}"
EVIDENCE_RE='level=error|level=fatal|panic:|FATA'

# Check if raw file exists
if [ ! -f "$RAW_FILE" ]; then
  echo "Error: Raw log file $RAW_FILE not found." >&2
  exit 1
fi

# Calculate R (raw evidence marker count)
# Handle exit code 1 if no matches are found
R=$(grep -Ec "$EVIDENCE_RE" "$RAW_FILE" || true)

# Run compressor and capture output and exit code
# We use a temp file to preserve formatting and handle trailing newlines accurately
TEMP_COMPRESSED=$(mktemp)
trap 'rm -f "$TEMP_COMPRESSED"' EXIT

set +e
"$COMPRESSOR" < "$RAW_FILE" > "$TEMP_COMPRESSED" 2>/dev/null
COMPRESSOR_EXIT=$?
set -e

# Calculate C (compressed evidence marker count)
C=$(grep -Ec "$EVIDENCE_RE" "$TEMP_COMPRESSED" || true)

# Check if compressed output is empty
IS_EMPTY=0
if [ ! -s "$TEMP_COMPRESSED" ]; then
  IS_EMPTY=1
fi

# Fallback decision
FALLBACK=0
if [ "$COMPRESSOR_EXIT" -ne 0 ] || [ "$IS_EMPTY" -eq 1 ] || [ "$C" -lt "$R" ]; then
  FALLBACK=1
fi

if [ "$FALLBACK" -eq 1 ]; then
  cat "$RAW_FILE"
  echo "RTK_FALLBACK=1" >&2
else
  cat "$TEMP_COMPRESSED"
  echo "RTK_FALLBACK=0" >&2
fi
