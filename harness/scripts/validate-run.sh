#!/bin/bash
# Validate a completed benchmark run's artifacts
# Usage: validate-run.sh <run-dir>
#   run-dir: path like skills/jake-benchmark/runs/qwen3.5:27b-q4_K_M__2026-04-20_120000
#
# Checks:
#   - manifest.json exists and is valid
#   - Each task has response.json, metadata.json
#   - Tasks with responses have non-empty response text
#   - session.jsonl files exist (warning only, not all tasks produce them)
#
# Exit 0 = valid, exit 1 = issues found
set -euo pipefail

RUN_DIR="${1:?Usage: validate-run.sh <run-dir>}"

if [ ! -d "$RUN_DIR" ]; then
    echo "ERROR: Run directory not found: $RUN_DIR"
    exit 1
fi

ERRORS=0
WARNINGS=0

error() { echo "  ERROR: $1"; ERRORS=$((ERRORS + 1)); }
warn()  { echo "  WARN: $1";  WARNINGS=$((WARNINGS + 1)); }
ok()    { echo "  OK: $1"; }

echo "=== Validating run: $(basename "$RUN_DIR") ==="

# Check manifest
MANIFEST="$RUN_DIR/manifest.json"
if [ ! -f "$MANIFEST" ]; then
    error "manifest.json missing"
else
    TASKS_RUN=$(python3 -c "import json; d=json.load(open('$MANIFEST')); print(d.get('tasks_run', 0))" 2>/dev/null || echo "0")
    FINISHED=$(python3 -c "import json; d=json.load(open('$MANIFEST')); print(d.get('finished', ''))" 2>/dev/null || echo "")
    MODEL=$(python3 -c "import json; d=json.load(open('$MANIFEST')); print(d.get('model', '?'))" 2>/dev/null || echo "?")
    THINKING=$(python3 -c "import json; d=json.load(open('$MANIFEST')); print(d.get('thinking_level', '?'))" 2>/dev/null || echo "?")

    ok "manifest.json: model=$MODEL, thinking=$THINKING, tasks=$TASKS_RUN, finished=$FINISHED"

    if [ -z "$FINISHED" ]; then
        error "manifest.json has empty 'finished' field (run may be incomplete)"
    fi
    if [ "$TASKS_RUN" -lt 20 ]; then
        warn "Only $TASKS_RUN tasks run (expected 22)"
    fi
fi

# Check each task directory
TASK_COUNT=0
RESPONSE_COUNT=0
ZERO_RESPONSE_TASKS=()

for task_dir in "$RUN_DIR"/*/; do
    [ -d "$task_dir" ] || continue
    task_name=$(basename "$task_dir")
    [ "$task_name" = "__pycache__" ] && continue
    TASK_COUNT=$((TASK_COUNT + 1))

    # Check response.json
    if [ ! -f "$task_dir/response.json" ]; then
        error "$task_name: missing response.json"
        continue
    fi

    # Validate response.json content
    RESP_INFO=$(python3 -c "
import json, sys
try:
    d = json.load(open('$task_dir/response.json'))
    responses = len(d.get('responses', d.get('all_responses', [])))
    tools = len(d.get('tool_calls', []))
    elapsed = d.get('elapsed_seconds', 0)
    natural = d.get('completed_naturally', False)
    entries = d.get('total_entries', 0)
    print(f'{responses} {tools} {elapsed} {natural} {entries}')
except Exception as e:
    print(f'PARSE_ERROR {e}')
" 2>/dev/null || echo "PARSE_ERROR")

    if echo "$RESP_INFO" | grep -q "PARSE_ERROR"; then
        error "$task_name: response.json is invalid JSON"
        continue
    fi

    read -r resp_count tool_count elapsed natural entries <<< "$RESP_INFO"

    if [ "$resp_count" = "0" ] && [ "$tool_count" = "0" ] && [ "$entries" = "0" ]; then
        error "$task_name: zero responses, tools, and entries (dead task)"
        ZERO_RESPONSE_TASKS+=("$task_name")
    elif [ "$resp_count" = "0" ]; then
        warn "$task_name: 0 responses but $tool_count tools, $entries entries (${elapsed}s)"
    else
        RESPONSE_COUNT=$((RESPONSE_COUNT + 1))
    fi

    # Check metadata.json
    if [ ! -f "$task_dir/metadata.json" ]; then
        warn "$task_name: missing metadata.json"
    fi

    # Check session.jsonl (informational only)
    if [ ! -f "$task_dir/session.jsonl" ]; then
        warn "$task_name: missing session.jsonl"
    fi
done

echo ""
echo "=== Summary ==="
echo "Tasks found: $TASK_COUNT"
echo "Tasks with responses: $RESPONSE_COUNT"
echo "Errors: $ERRORS"
echo "Warnings: $WARNINGS"

if [ ${#ZERO_RESPONSE_TASKS[@]} -gt 0 ]; then
    echo "Dead tasks: ${ZERO_RESPONSE_TASKS[*]}"
fi

if [ $ERRORS -gt 0 ]; then
    echo "STATUS: INVALID"
    exit 1
else
    echo "STATUS: VALID"
    exit 0
fi
