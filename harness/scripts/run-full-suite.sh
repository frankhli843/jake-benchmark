#!/bin/bash
# run-full-suite.sh - Run entire benchmark suite locally on Pi
# No SSH needed. Pulls models via Ollama on Desktop (Tailscale), swaps Jake config, runs tasks.
# Usage: nohup bash skills/jake-benchmark/scripts/run-full-suite.sh > /tmp/benchmark-suite.log 2>&1 &
set -euo pipefail

SCRIPT_DIR="$HOME/.openclaw/workspace/skills/jake-benchmark/scripts"
LOG_DIR="$HOME/.openclaw/workspace/skills/jake-benchmark/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/suite-$(date +%Y%m%d_%H%M%S).log"
OLLAMA_HOST="http://100.69.102.71:11434"
OPENCLAW_BIN="/home/linuxbrew/.linuxbrew/bin/openclaw"
CONFIG="$HOME/.openclaw/openclaw.json"

get_context_window() {
  case "$1" in
    gemma4:26b) echo 131072 ;;
    gemma4:31b) echo 32768 ;;
    gemma4:e4b) echo 131072 ;;
    *) echo 131072 ;;
  esac
}

# Models with native thinking support - test all 4 levels
THINKING_MODELS=("qwen3.5:27b-q4_K_M" "qwen3.5:35b" "qwen3:8b" "deepseek-r1:8b" "gemma4:26b")
THINKING_LEVELS=("off" "low" "medium" "high")

# Models without thinking support - test once at off
NO_THINKING_MODELS=("glm-4.7-flash" "nemotron-3-nano:30b" "lfm2" "gemma3:4b" "llama4:8b")

ALL_MODELS=("${THINKING_MODELS[@]}" "${NO_THINKING_MODELS[@]}")
TOTAL_RUNS=$(( ${#THINKING_MODELS[@]} * ${#THINKING_LEVELS[@]} + ${#NO_THINKING_MODELS[@]} ))

exec > >(tee -a "$LOG") 2>&1

echo "=== Jake Benchmark Suite (standalone) ==="
echo "Thinking models: ${THINKING_MODELS[*]}"
echo "No-thinking models: ${NO_THINKING_MODELS[*]}"
echo "Total runs: $TOTAL_RUNS"
echo "Ollama: $OLLAMA_HOST"
echo "Started: $(date)"

# Pull missing models via Ollama API
for model in "${ALL_MODELS[@]}"; do
  if curl -sf "$OLLAMA_HOST/api/tags" | python3 -c "import sys,json; models=[m['name'] for m in json.loads(sys.stdin.read()).get('models',[])]; sys.exit(0 if any('$model' in m for m in models) else 1)" 2>/dev/null; then
    echo "✅ $model already available"
  else
    echo "⬇️ Pulling $model..."
    curl -sf "$OLLAMA_HOST/api/pull" -d "{\"name\": \"$model\"}" --no-buffer | python3 -c "
import sys,json
for line in sys.stdin:
    try:
        d=json.loads(line)
        s=d.get('status','')
        if 'pulling' in s and d.get('total'):
            pct=int(d.get('completed',0)/d['total']*100)
            print(f'\r  {pct}% ({d.get(\"completed\",0)//1048576}MB/{d[\"total\"]//1048576}MB)', end='', flush=True)
        elif s == 'success':
            print(f'\n✅ $model pulled')
    except: pass
" || echo "❌ Failed to pull $model"
  fi
done

echo ""
echo "All models ready. Starting benchmarks..."

run_benchmark() {
  local model="$1" thinking="$2"
  local context_window
  context_window="$(get_context_window "$model")"
  
  # Skip if this combo already has a completed run (check runs/ for matching manifest)
  local RUNS_DIR="$HOME/.openclaw/workspace/skills/jake-benchmark/runs"
  local already_done=$(python3 -c "
import json, os, glob
for mf in glob.glob('$RUNS_DIR/*/manifest.json'):
    try:
        d = json.load(open(mf))
        if d.get('model') == '$model' and d.get('thinking_level') == '$thinking' and d.get('tasks_run', 0) >= 20:
            print('yes')
            break
    except: pass
" 2>/dev/null)
  
  if [ "$already_done" = "yes" ]; then
    echo ""
    echo "⏭️  SKIP: $model @ thinking=$thinking (already completed)"
    return 0
  fi

  echo ""
  echo "========================================="
  echo ">>> $model @ thinking=$thinking @ context=$context_window ($(date))"
  echo "========================================="
  
  # Update config
  python3 -c "
import json
with open('$CONFIG') as f:
    cfg = json.load(f)
cfg['agents']['defaults']['model']['primary'] = 'ollama/$model'
cfg['agents']['defaults']['thinkingDefault'] = '$thinking'
ollama_cfg = cfg.setdefault('models', {}).setdefault('providers', {}).setdefault('ollama', {})
ollama_cfg['models'] = [{'id': '$model', 'name': '$model', 'contextWindow': $context_window, 'maxTokens': 16384}]
with open('$CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)
print('Config: $model @ thinking=$thinking @ context=$context_window')
"
  
  # Reset memory
  echo "# MEMORY.md" > "$HOME/.openclaw/workspace/MEMORY.md"
  rm -f "$HOME/.openclaw/workspace/memory/"*.md
  rm -f "$HOME/.openclaw/agents/main/sessions/"*.jsonl
  echo "Memory reset"
  
  # Restart gateway
  PATH="/home/linuxbrew/.linuxbrew/bin:$PATH" $OPENCLAW_BIN gateway restart 2>&1 || true
  echo "Waiting 20s for gateway..."
  sleep 20
  
  # Health check
  if curl -sf http://127.0.0.1:18789/health > /dev/null 2>&1; then
    echo "✅ Gateway healthy"
  else
    echo "⚠️ Gateway not healthy, waiting 20s more..."
    sleep 20
  fi
  
  # Run benchmark
  echo "Running benchmark tasks..."
  cd "$HOME/.openclaw/workspace"
  THINKING_LEVEL="$thinking" JAKE_INCLUDE_EXPERIMENTAL=1 bash "$SCRIPT_DIR/run-benchmark.sh" "$model" 2>&1
  
  echo "=== Done: $model @ $thinking ($(date)) ==="
}

# Phase 1: Thinking models x all levels
echo ""
echo "=== PHASE 1: Thinking Models ==="
for model in "${THINKING_MODELS[@]}"; do
  for thinking in "${THINKING_LEVELS[@]}"; do
    run_benchmark "$model" "$thinking"
  done
done

# Phase 2: Non-thinking models x off only
echo ""
echo "=== PHASE 2: Non-Thinking Models ==="
for model in "${NO_THINKING_MODELS[@]}"; do
  run_benchmark "$model" "off"
done

# Restore original
echo ""
echo "Restoring qwen3.5:27b-q4_K_M @ thinking=off..."
python3 -c "
import json
with open('$CONFIG') as f:
    cfg = json.load(f)
cfg['agents']['defaults']['model']['primary'] = 'ollama/qwen3.5:27b-q4_K_M'
cfg['agents']['defaults']['thinkingDefault'] = 'off'
cfg['models']['providers']['ollama']['models'] = [{'id': 'qwen3.5:27b-q4_K_M', 'name': 'qwen3.5:27b-q4_K_M', 'contextWindow': 131072, 'maxTokens': 16384}]
with open('$CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)
"
PATH="/home/linuxbrew/.linuxbrew/bin:$PATH" $OPENCLAW_BIN gateway restart 2>&1 || true

echo ""
echo "=== ALL DONE: $(date) ==="
echo "Total runs: $TOTAL_RUNS"
echo "Log: $LOG"
