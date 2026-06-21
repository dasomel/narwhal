#!/bin/bash
# run-arm.sh <raw|rtk> <out.jsonl>
# Usage: ./run-arm.sh raw out-raw.jsonl
#        ./run-arm.sh rtk out-rtk.jsonl

set -euo pipefail

ARM="$1"
OUT_FILE="$2"

if [ "$ARM" != "raw" ] && [ "$ARM" != "rtk" ]; then
  echo "Usage: $0 <raw|rtk> <out.jsonl>" >&2
  exit 1
fi

CORPUS_DIR="corpus"
QUESTIONS_FILE="questions.json"

# Check if required directories and files exist
if [ ! -d "$CORPUS_DIR" ]; then
  echo "Error: Corpus directory '$CORPUS_DIR' not found." >&2
  exit 1
fi

if [ ! -f "$QUESTIONS_FILE" ]; then
  echo "Error: Questions file '$QUESTIONS_FILE' not found." >&2
  exit 1
fi

# Read questions
QUESTIONS_CONTENT=$(cat "$QUESTIONS_FILE")

# Token approximation function (words * 1.3)
approximate_tokens() {
  local text="$1"
  local words
  words=$(echo "$text" | wc -w)
  # Using awk for floating point math
  awk -v w="$words" 'BEGIN { printf "%d", w * 1.3 + 0.5 }'
}

# Clear/Initialize output file
echo -n "" > "$OUT_FILE"

# Iterate over corpus logs
for log_path in "$CORPUS_DIR"/*.log; do
  [ -e "$log_path" ] || continue
  filename=$(basename "$log_path")
  id="${filename%.log}"
  
  # Determine log content depending on arm
  fallback=0
  if [ "$ARM" = "rtk" ]; then
    # Run rtk-wrap.sh, capture stdout and stderr (for RTK_FALLBACK)
    # Note: compressor/rtk-wrap.sh is used
    TEMP_ERR=$(mktemp)
    log_content=$(./compressor/rtk-wrap.sh "$log_path" 2>"$TEMP_ERR")
    fallback_val=$(cat "$TEMP_ERR" | grep "RTK_FALLBACK=" | cut -d'=' -f2 || echo "0")
    fallback=$((fallback_val))
    rm -f "$TEMP_ERR"
  else
    log_content=$(cat "$log_path")
    fallback=0
  fi
  
  # Construct prompt
  PROMPT="--- LOG START ---
$log_content
--- LOG END ---

Questions:
$QUESTIONS_CONTENT

Please answer the questions. Format your output exactly as:
PHASE: <phase>
ROOT_CAUSE: <root_cause>"

  input_tokens=$(approximate_tokens "$PROMPT")
  
  # Invoke agent
  if [ -n "${AGENT_CMD:-}" ]; then
    # Set AGENT_CMD to a real LLM CLI for actual runs (e.g. AGENT_CMD="llm-cli")
    agent_output=$(echo "$PROMPT" | eval "$AGENT_CMD")
    
    # Extract answers from LLM output
    answer_phase=$(echo "$agent_output" | grep -i "^PHASE:" | head -n 1 | cut -d':' -f2- | xargs || echo "")
    answer_root_cause=$(echo "$agent_output" | grep -i "^ROOT_CAUSE:" | head -n 1 | cut -d':' -f2- | xargs || echo "")
  else
    # STUB agent: echoes prompt verbatim as response.
    # To support clean wiring checks, we parse answers from the echoed prompt (which contains the log content).
    agent_output="$PROMPT"
    
    if echo "$agent_output" | grep -q "timed out waiting for volumesnapshot"; then
      answer_phase="PartiallyFailed"
      answer_root_cause="volumesnapshot timed out"
    elif echo "$agent_output" | grep -q "panic:"; then
      answer_phase="Failed"
      answer_root_cause="plugin panic"
    else
      answer_phase="Completed"
      answer_root_cause=""
    fi
  fi
  
  output_tokens=$(approximate_tokens "$agent_output")
  
  # Construct JSON line
  json_line=$(printf '{"id":"%s","arm":"%s","input_tokens":%d,"output_tokens":%d,"fallback":%d,"answer_phase":"%s","answer_root_cause":"%s"}' \
    "$id" "$ARM" "$input_tokens" "$output_tokens" "$fallback" "$answer_phase" "$answer_root_cause")
  
  echo "$json_line" >> "$OUT_FILE"
done

echo "Done running arm '$ARM'. Results saved to '$OUT_FILE'."
