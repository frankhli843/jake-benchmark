#!/bin/bash
# Run benchmark for a single model: verify on Ollama → swap Jake config → restart gateway → run suite
# Usage: run-model-benchmark.sh "model-name"
# Designed to be called from cron or sequentially
set -euo pipefail

MODEL="${1:?Usage: run-model-benchmark.sh 'model-name' [thinking-level]}"
THINKING="${2:-}"  # off, low, medium, high (empty = run all levels)
PI="frank@100.108.252.124"
OPENCLAW="/home/linuxbrew/.linuxbrew/bin/openclaw"
OPENCLAW_BIN_DIR="$(dirname "$OPENCLAW")"

get_context_window() {
  case "$1" in
    # gemma4 dense/MoE variants: always 32768 per cron hard rule.
    # gemma4:31b (19GB) may spill ~14% to CPU at 32k — acceptable tradeoff.
    gemma4:26b) echo 32768 ;;
    gemma4:31b) echo 32768 ;;
    gemma4:e4b) echo 131072 ;;
    *) echo 131072 ;;
  esac
}

CONTEXT_WINDOW="$(get_context_window "$MODEL")"

# If no thinking level specified, run all levels sequentially
if [ -z "$THINKING" ]; then
  echo "=== Running all thinking levels for $MODEL ==="
  for level in off low medium high; do
    echo ""
    echo ">>>>>>>>>> $MODEL @ thinking=$level <<<<<<<<<<"
    bash "$0" "$MODEL" "$level"
  done
  exit $?
fi

LOG="/tmp/jake-benchmark-${MODEL//[:\/]/_}-think-${THINKING}.log"

echo "=== Benchmark: $MODEL (thinking=$THINKING, context=$CONTEXT_WINDOW) ===" | tee "$LOG"
echo "Started: $(date)" | tee -a "$LOG"

# Step 1: Verify model is available in Ollama
echo "Verifying $MODEL in Ollama..." | tee -a "$LOG"
if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
  echo "❌ Model $MODEL not found in Ollama. Pulling..." | tee -a "$LOG"
  ollama pull "$MODEL" >> "$LOG" 2>&1 || { echo "❌ Pull failed, aborting" | tee -a "$LOG"; exit 1; }
fi

# Step 2: Pre-warm model with FULL context window via Ollama API
# Uses api/chat with streaming and think:true so thinking models (gemma4) return
# content in the thinking field. Falls back to api/generate for non-thinking models.
# This also validates that the model can actually load at the target num_ctx without VRAM deadlock.
echo "Pre-warming $MODEL with num_ctx=$CONTEXT_WINDOW..." | tee -a "$LOG"
OLLAMA_TEXT=$(timeout 300 curl -s http://localhost:11434/api/chat \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello\"}],\"stream\":true,\"think\":true,\"keep_alive\":\"6h\",\"options\":{\"num_predict\":50,\"num_ctx\":$CONTEXT_WINDOW}}" 2>&1 \
  | python3 -c "
import json, sys
tokens = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        msg = d.get('message', {})
        # Collect both content and thinking tokens
        r = msg.get('content', '') or msg.get('thinking', '') or d.get('response', '')
        if r:
            tokens.append(r)
    except: pass
print(''.join(tokens)[:200])
" 2>/dev/null || echo "")
if [ -z "$OLLAMA_TEXT" ]; then
  echo "❌ Model $MODEL failed to generate response at num_ctx=$CONTEXT_WINDOW, aborting" | tee -a "$LOG"
  echo "  This likely means the model overflows GPU VRAM at this context size." | tee -a "$LOG"
  echo "  Check: ollama ps, nvidia-smi, OLLAMA_GPU_OVERHEAD setting" | tee -a "$LOG"
  exit 1
fi
echo "✅ Ollama pre-warm OK (ctx=$CONTEXT_WINDOW): $OLLAMA_TEXT" | tee -a "$LOG"

# Step 3: Update Jake's config on Pi
echo "Configuring Jake for $MODEL (context=$CONTEXT_WINDOW)..." | tee -a "$LOG"
ssh "$PI" "python3 << PYEOF
import json
with open('/home/frank/.openclaw/openclaw.json') as f:
    cfg = json.load(f)

cfg['agents']['defaults']['model']['primary'] = 'ollama/$MODEL'
cfg['agents']['defaults']['thinkingDefault'] = '$THINKING'

# Ensure models config is correct format
ollama_cfg = cfg.setdefault('models', {}).setdefault('providers', {}).setdefault('ollama', {})
model_entry = {'id': '$MODEL', 'name': '$MODEL', 'contextWindow': $CONTEXT_WINDOW, 'maxTokens': 16384}
if isinstance(ollama_cfg.get('models'), dict):
    ollama_cfg['models'] = [model_entry]
elif isinstance(ollama_cfg.get('models'), list):
    # Replace existing model entries
    ollama_cfg['models'] = [model_entry]
else:
    ollama_cfg['models'] = [model_entry]

with open('/home/frank/.openclaw/openclaw.json', 'w') as f:
    json.dump(cfg, f, indent=2)
print('Config updated with contextWindow=$CONTEXT_WINDOW')
PYEOF" >> "$LOG" 2>&1

# Step 4: Reset memory to clean state (each test starts fresh)
echo "Resetting Jake's memory..." | tee -a "$LOG"
ssh "$PI" "
  # Clear MEMORY.md to default
  echo '# MEMORY.md' > ~/.openclaw/workspace/MEMORY.md
  # Clear daily memory files
  rm -f ~/.openclaw/workspace/memory/*.md
  # Clear session transcripts so no stale context bleeds between tests
  rm -f ~/.openclaw/agents/main/sessions/*.jsonl
  # Prune sessions.json to avoid bloat (>200 entries causes agent startup to hang on Pi)
  python3 -c \"
import json, os
p = os.path.expanduser('~/.openclaw/agents/main/sessions/sessions.json')
if os.path.exists(p):
    with open(p) as f: d = json.load(f)
    if len(d) > 20:
        items = sorted(((k,v) for k,v in d.items() if isinstance(v,dict)), key=lambda x: x[1].get('updatedAt',0), reverse=True)[:10]
        with open(p,'w') as f: json.dump(dict(items), f, indent=2)
        print(f'Pruned sessions.json: {len(d)} -> {len(items)}')
    else:
        print(f'sessions.json OK ({len(d)} entries)')
\" 2>&1
  echo 'Memory reset complete'
" >> "$LOG" 2>&1

# Step 5: Restart Jake's gateway
echo "Restarting Jake's gateway..." | tee -a "$LOG"
ssh "$PI" "
  cat > ~/.openclaw/workspace/state/restart-reason.md << REASON
datetime: \$(date -u +%Y-%m-%dT%H:%M:%SZ)
reason: Benchmark run for $MODEL (thinking=$THINKING)
changes: Config updated, memory reset, sessions pruned
REASON
  PATH=$OPENCLAW_BIN_DIR:\$PATH openclaw gateway restart
" >> "$LOG" 2>&1 || true
echo "Waiting 20s for gateway startup..." | tee -a "$LOG"
sleep 20

# Step 6: Verify gateway is up
GW_STATUS=$(ssh "$PI" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/health" 2>/dev/null || echo "000")
if [ "$GW_STATUS" != "200" ]; then
  echo "❌ Gateway not healthy (HTTP $GW_STATUS), waiting 20s more..." | tee -a "$LOG"
  sleep 20
  GW_STATUS=$(ssh "$PI" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/health" 2>/dev/null || echo "000")
  if [ "$GW_STATUS" != "200" ]; then
    echo "❌ Gateway failed to start for $MODEL, aborting" | tee -a "$LOG"
    exit 1
  fi
fi
echo "✅ Gateway healthy" | tee -a "$LOG"

# Step 7: Smoke test - verify Pi can reach Ollama and get a response
# Model is already warm from Step 2 pre-warm, so this should be fast (< 60s).
echo "Smoke testing Ollama from Pi..." | tee -a "$LOG"
SMOKE_TEXT=$(timeout 120 ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=30 "$PI" \
  "timeout 90 curl -s http://100.69.102.71:11434/api/chat -d '{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello\"}],\"stream\":true,\"think\":true,\"keep_alive\":\"6h\",\"options\":{\"num_predict\":30,\"num_ctx\":$CONTEXT_WINDOW}}'" 2>/dev/null \
  | python3 -c "
import json, sys
tokens = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        msg = d.get('message', {})
        r = msg.get('content', '') or msg.get('thinking', '') or d.get('response', '')
        if r: tokens.append(r)
    except: pass
print(''.join(tokens)[:200])
" 2>/dev/null || echo "")
if [ -z "$SMOKE_TEXT" ]; then
  echo "❌ Pi cannot get a response from Ollama ($MODEL), aborting" | tee -a "$LOG"
  exit 1
fi
echo "✅ Smoke OK from Pi: $SMOKE_TEXT" | tee -a "$LOG"

# Step 7b: Gateway-level smoke test - verify OpenClaw can get a response from the model
# Model is warm from steps 2+7. Gateway adapter failures should surface fast.
# Reduced timeouts: model is pre-warmed, so if gateway can't get a response in
# 60-90s, something is fundamentally broken. Don't waste 5 min on a dead model.
# Pi startup is slow (~30-60s for openclaw agent to create a session).
# Timeouts must account for startup + model inference.
# Smoke test uses JAKE_SMOKE_MODE=1: exit as soon as the first complete response
# arrives, instead of waiting IDLE_SECONDS after the response. This cuts smoke
# from ~150s to ~90s for thinking models. NO_ACTIVITY_ABORT is the real fail-fast
# knob: if nothing appears after this many seconds, the model is dead.
# Smoke timeouts: Ollama smoke (Step 7) already proved the model works from Pi,
# so gateway smoke just verifies the OpenClaw adapter path. Thinking models at
# high/xhigh can be silent for 2+ minutes while generating thinking tokens
# (e.g. qwen3.6:35b takes ~132s at high). Timeouts must accommodate this
# without waiting forever for a genuinely broken adapter.
SMOKE_NO_ACTIVITY=60
SMOKE_IDLE=10
SMOKE_NO_RESPONSE=45
SMOKE_DISPATCH_TIMEOUT=90
SMOKE_OUTER_TIMEOUT=120
case "$THINKING" in
  high|xhigh)
    SMOKE_NO_ACTIVITY=180    # thinking models can be silent 2+ min
    SMOKE_NO_RESPONSE=150
    SMOKE_DISPATCH_TIMEOUT=240
    SMOKE_OUTER_TIMEOUT=270
    ;;
  medium)
    SMOKE_NO_ACTIVITY=90
    SMOKE_NO_RESPONSE=75
    SMOKE_DISPATCH_TIMEOUT=150
    SMOKE_OUTER_TIMEOUT=180
    ;;
esac

# Keepalive ping: model may have been idle during gateway restart + wait.
# Re-poke it so the gateway smoke doesn't pay model-load latency.
echo "Sending model keepalive before gateway smoke..." | tee -a "$LOG"
timeout 30 curl -s http://localhost:11434/api/chat \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"stream\":false,\"keep_alive\":\"6h\",\"options\":{\"num_predict\":1,\"num_ctx\":$CONTEXT_WINDOW}}" > /dev/null 2>&1 || true

echo "Gateway smoke test (OpenClaw agent, up to ${SMOKE_OUTER_TIMEOUT}s)..." | tee -a "$LOG"
GW_SMOKE=$(timeout "$SMOKE_OUTER_TIMEOUT" ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=30 "$PI" \
  "PATH=$OPENCLAW_BIN_DIR:\$PATH JAKE_NO_ACTIVITY_ABORT=$SMOKE_NO_ACTIVITY JAKE_IDLE_SECONDS=$SMOKE_IDLE JAKE_NO_RESPONSE_ABORT=$SMOKE_NO_RESPONSE JAKE_SMOKE_MODE=1 python3 \$HOME/.openclaw/workspace/skills/jake-benchmark/scripts/jake-dispatch.py 'Say hello in one sentence.' smoke-test-$$ $SMOKE_DISPATCH_TIMEOUT" 2>&1 || echo "TIMEOUT")
echo "  Gateway smoke: $GW_SMOKE" | tee -a "$LOG"
# Check the smoke result file
GW_SMOKE_RESP=$(timeout 10 ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI" \
  "python3 -c \"import json; d=json.load(open('/tmp/jake-response-smoke-test-$$.json')); print(len(d.get('responses',[])), len(d.get('tool_calls',[])), d.get('total_entries',0))\"" 2>/dev/null || echo "0 0 0")
# Clean up smoke test session artifacts on Pi
ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI" "rm -f /tmp/jake-response-smoke-test-$$.json" 2>/dev/null || true
GW_RESP_COUNT=$(echo "$GW_SMOKE_RESP" | awk '{print $1}')
GW_TOOL_COUNT=$(echo "$GW_SMOKE_RESP" | awk '{print $2}')
GW_ENTRIES=$(echo "$GW_SMOKE_RESP" | awk '{print $3}')
if [ "${GW_RESP_COUNT:-0}" = "0" ] && [ "${GW_TOOL_COUNT:-0}" = "0" ] && [ "${GW_ENTRIES:-0}" = "0" ]; then
  echo "❌ Gateway smoke test FAILED: 0 responses, 0 tools, 0 entries" | tee -a "$LOG"
  echo "  Model does not work through OpenClaw adapter. Aborting to avoid wasting hours." | tee -a "$LOG"
  echo "  Debug: check Pi gateway logs (journalctl --user -u openclaw on Pi)" | tee -a "$LOG"
  exit 1
elif [ "${GW_RESP_COUNT:-0}" = "0" ] && [ "${GW_ENTRIES:-0}" != "0" ]; then
  echo "⚠️ Gateway smoke: $GW_ENTRIES entries but 0 responses (model may produce empty content)" | tee -a "$LOG"
  echo "  Proceeding with benchmark but results may have 0-response tasks." | tee -a "$LOG"
else
  echo "✅ Gateway smoke OK ($GW_RESP_COUNT responses, $GW_TOOL_COUNT tools, $GW_ENTRIES entries)" | tee -a "$LOG"
fi

# Step 8: Ensure test site is running
# Do not rely on remote exit codes, always verify by curling for HTTP 200.
echo "Ensuring test site is running..." | tee -a "$LOG"
TEST_URL="http://127.0.0.1:3456/test/job-board"
REMOTE_START_TEST_SITE_CMD='cd ~/.openclaw/workspace/test-site || exit 1; if [ -x /home/frank/.nvm/versions/node/v24.12.0/bin/node ]; then NODE_BIN=/home/frank/.nvm/versions/node/v24.12.0/bin/node; else NODE_BIN=$(command -v node); fi; nohup "$NODE_BIN" server.js > /dev/null 2>&1 &'

for attempt in 1 2 3; do
  code=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI" "curl -sS --connect-timeout 2 --max-time 4 -o /dev/null -w '%{http_code}' $TEST_URL 2>/dev/null || true" 2>/dev/null | tr -d '\r\n')
  if [ "$code" = "200" ]; then
    echo "✅ Test site OK" | tee -a "$LOG"
    break
  fi

  echo "⚠️ Test site not ready (code=${code:-empty}), starting it (attempt $attempt)..." | tee -a "$LOG"
  timeout 25 ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 \
    "$PI" "bash -lc $(printf %q \"$REMOTE_START_TEST_SITE_CMD\")" >> "$LOG" 2>&1 || true
  sleep 3

  code2=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI" "curl -sS --connect-timeout 2 --max-time 4 -o /dev/null -w '%{http_code}' $TEST_URL 2>/dev/null || true" 2>/dev/null | tr -d '\r\n')
  if [ "$code2" = "200" ]; then
    echo "✅ Test site OK" | tee -a "$LOG"
    break
  fi

  if [ "$attempt" = "3" ]; then
    echo "❌ Could not verify/start test site on Pi (code=$code2), aborting" | tee -a "$LOG"
    exit 1
  fi
  sleep 2
done

# Step 9: Run the full benchmark
echo "Running full benchmark..." | tee -a "$LOG"
# Set per-task fail-fast timeouts based on thinking level.
# Model is already warm, so if a task gets no activity in 90-120s, it's stuck.
BENCH_NO_ACTIVITY=90
BENCH_IDLE=60
case "$THINKING" in
  high|xhigh) BENCH_NO_ACTIVITY=120; BENCH_IDLE=90 ;;
  medium)     BENCH_NO_ACTIVITY=105; BENCH_IDLE=75 ;;
esac
BENCH_NO_RESPONSE=180
case "$THINKING" in
  high|xhigh) BENCH_NO_RESPONSE=240 ;;
  medium)     BENCH_NO_RESPONSE=210 ;;
esac
echo "Per-task fail-fast: NO_ACTIVITY_ABORT=${BENCH_NO_ACTIVITY}s, IDLE=${BENCH_IDLE}s, NO_RESPONSE=${BENCH_NO_RESPONSE}s" | tee -a "$LOG"
# Run benchmark on Pi via nohup so it survives SSH disconnects
# The script writes results to /tmp/jake-response-bench-*.json on Pi
ssh "$PI" "cd ~/.openclaw/workspace && nohup env THINKING_LEVEL='$THINKING' JAKE_NO_ACTIVITY_ABORT='$BENCH_NO_ACTIVITY' JAKE_IDLE_SECONDS='$BENCH_IDLE' JAKE_NO_RESPONSE_ABORT='$BENCH_NO_RESPONSE' JAKE_MAX_CONSECUTIVE_DEAD=2 JAKE_INCLUDE_EXPERIMENTAL=1 bash skills/jake-benchmark/scripts/run-benchmark.sh '$MODEL' > /tmp/jake-benchmark-run.log 2>&1 &
echo \$!" > /tmp/bench-pid.txt

REMOTE_PID=$(cat /tmp/bench-pid.txt)
echo "Benchmark running on Pi (PID: $REMOTE_PID)" | tee -a "$LOG"

# Poll for completion (check if PID is still alive)
# Max wait: 6 hours (22 tasks * ~15 min each worst case)
MAX_POLL=$((6 * 3600))
POLL_START=$(date +%s)
while ssh -o ConnectTimeout=8 "$PI" "kill -0 $REMOTE_PID 2>/dev/null" 2>/dev/null; do
  ELAPSED=$(( $(date +%s) - POLL_START ))
  if [ "$ELAPSED" -ge "$MAX_POLL" ]; then
    echo "⚠️ Benchmark exceeded ${MAX_POLL}s poll timeout, collecting partial results" | tee -a "$LOG"
    break
  fi
  echo "  ... still running (${ELAPSED}s elapsed)" >> "$LOG"
  sleep 60
done

# Grab the log
ssh "$PI" "cat /tmp/jake-benchmark-run.log" >> "$LOG" 2>&1

# Step 10: Copy results to Desktop
echo "Copying results..." | tee -a "$LOG"
RESULT_DIR="$HOME/.openclaw/workspace/skills/jake-benchmark/results/${MODEL}__think-${THINKING}"
mkdir -p "$RESULT_DIR"
scp -r "$PI:~/.openclaw/workspace/skills/jake-benchmark/results/$MODEL/"* "$RESULT_DIR/" 2>/dev/null || true
mkdir -p "$HOME/.openclaw/workspace/skills/jake-benchmark/runs/"
scp -r "$PI":~/.openclaw/workspace/skills/jake-benchmark/runs/"${MODEL}__"* "$HOME/.openclaw/workspace/skills/jake-benchmark/runs/" 2>/dev/null || true

echo "" | tee -a "$LOG"
echo "=== Benchmark Complete: $MODEL (thinking=$THINKING) ===" | tee -a "$LOG"
echo "Finished: $(date)" | tee -a "$LOG"
