#!/bin/bash
# Jake Benchmark Harness entrypoint
# Usage:
#   docker run jake-harness scan-and-qa          # Scan LocalLLaMA + QA dashboard (default)
#   docker run jake-harness scan                 # Scan LocalLLaMA only
#   docker run jake-harness qa                   # QA dashboard only
#   docker run jake-harness validate <run-dir>   # Validate a benchmark run
#   docker run jake-harness benchmark <model>    # Run benchmark on Pi via SSH
#                                                  (requires SSH key mount)
set -euo pipefail

COMMAND="${1:-scan-and-qa}"
shift || true

case "$COMMAND" in
    scan-and-qa)
        echo "=== Jake Benchmark: Scan + QA ==="
        echo ""
        echo "--- Step 1: Scanning r/LocalLLaMA ---"
        python3 scripts/scan-localllama.py \
            --state-file state/localllama-seen.json \
            --summary
        echo ""
        echo "--- Step 2: Dashboard QA ---"
        bash scripts/qa-dashboard.sh
        ;;

    scan)
        echo "=== Jake Benchmark: LocalLLaMA Scan ==="
        python3 scripts/scan-localllama.py \
            --state-file state/localllama-seen.json \
            "$@"
        ;;

    scan-json)
        # Output raw JSON (for piping to other tools)
        python3 scripts/scan-localllama.py \
            --state-file state/localllama-seen.json \
            "$@"
        ;;

    qa)
        echo "=== Jake Benchmark: Dashboard QA ==="
        bash scripts/qa-dashboard.sh "$@"
        ;;

    validate)
        echo "=== Jake Benchmark: Run Validation ==="
        bash scripts/validate-run.sh "$@"
        ;;

    benchmark)
        # Run benchmark on Pi via SSH
        # Requires: -v ~/.ssh:/root/.ssh:ro -v /path/to/results:/harness/results
        MODEL="${1:?Usage: docker run jake-harness benchmark <model-name> [thinking-level]}"
        THINKING="${2:-off}"
        PI_HOST="${PI_HOST:-frank@100.108.252.124}"

        echo "=== Jake Benchmark: Remote Run ==="
        echo "Model: $MODEL, Thinking: $THINKING"
        echo "Pi: $PI_HOST"

        if [ ! -f /root/.ssh/id_ed25519 ] && [ ! -f /root/.ssh/id_rsa ]; then
            echo "ERROR: SSH key not found. Mount with: -v ~/.ssh:/root/.ssh:ro"
            exit 1
        fi

        # Verify SSH connectivity
        if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$PI_HOST" "echo ok" 2>/dev/null; then
            echo "ERROR: Cannot connect to Pi ($PI_HOST)"
            exit 1
        fi

        echo "Starting benchmark on Pi..."
        ssh "$PI_HOST" "cd ~/.openclaw/workspace && THINKING_LEVEL='$THINKING' bash skills/jake-benchmark/scripts/run-benchmark.sh '$MODEL'" 2>&1

        echo ""
        echo "Validating results..."
        # Find the latest run for this model
        LATEST_RUN=$(ssh "$PI_HOST" "ls -td ~/.openclaw/workspace/skills/jake-benchmark/runs/${MODEL}__* 2>/dev/null | head -1")
        if [ -n "$LATEST_RUN" ]; then
            echo "Latest run: $LATEST_RUN"
            # Copy and validate
            mkdir -p "results/$(basename "$LATEST_RUN")"
            scp -r "$PI_HOST:$LATEST_RUN/"* "results/$(basename "$LATEST_RUN")/" 2>/dev/null
            bash scripts/validate-run.sh "results/$(basename "$LATEST_RUN")"
        else
            echo "WARNING: No run directory found for $MODEL"
        fi
        ;;

    help|--help|-h)
        echo "Jake Benchmark Harness"
        echo ""
        echo "Commands:"
        echo "  scan-and-qa    Scan LocalLLaMA + QA dashboard (default)"
        echo "  scan           Scan r/LocalLLaMA for new models (--summary or raw JSON)"
        echo "  scan-json      Output scan results as raw JSON"
        echo "  qa             QA smoke test the dashboard site"
        echo "  validate       Validate a benchmark run directory"
        echo "  benchmark      Run benchmark on Pi via SSH"
        echo ""
        echo "Examples:"
        echo "  docker run jake-harness"
        echo "  docker run jake-harness scan --summary"
        echo "  docker run jake-harness qa https://frankhli843.github.io/jake-benchmark/"
        echo "  docker run -v \$(pwd)/state:/harness/state jake-harness scan --summary"
        echo "  docker run -v ~/.ssh:/root/.ssh:ro jake-harness benchmark qwen3.6:35b high"
        ;;

    *)
        echo "Unknown command: $COMMAND"
        echo "Run with 'help' for usage"
        exit 1
        ;;
esac
