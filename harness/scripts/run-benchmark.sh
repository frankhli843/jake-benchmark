#!/bin/bash
# Run Jake benchmark suite — one task at a time, wait for full completion
# Usage: run-benchmark.sh "model-name" [task-filter]
#   task-filter: optional, run only tasks matching this string (e.g. "phishing" or "medium")
#
# Runs ON the Pi (or via SSH from Desktop)
set -euo pipefail

MODEL_NAME="${1:?Usage: run-benchmark.sh 'model-name' [task-filter]}"
TASK_FILTER="${2:-}"

SKILL_DIR="$HOME/.openclaw/workspace/skills/jake-benchmark"
TASKS_FILE="$SKILL_DIR/tasks.json"
RUN_DATE=$(date +%Y-%m-%d_%H%M%S)
RUN_ID="${MODEL_NAME}__${RUN_DATE}"
RESULTS_DIR="$SKILL_DIR/results/$MODEL_NAME"
# Archive: permanent dated copy of every run
ARCHIVE_DIR="$SKILL_DIR/runs/${RUN_ID}"
# Use the skill-local dispatcher as the single source of truth.
DISPATCH="$SKILL_DIR/scripts/jake-dispatch.py"
GOG_STATE="$HOME/.config/gogcli/state"
SESSIONS_DIR="$HOME/.openclaw/agents/main/sessions"
MEMORY_DIR="$HOME/.openclaw/workspace/memory"

mkdir -p "$RESULTS_DIR"
mkdir -p "$ARCHIVE_DIR"

# Seed stable mock fixtures BEFORE snapshotting baseline state.
python3 "$SKILL_DIR/scripts/seed-mock-gog-state.py" >/dev/null 2>&1 || true

# Snapshot clean baseline state BEFORE any tasks run
BASELINE_DIR="$SKILL_DIR/baseline"
mkdir -p "$BASELINE_DIR/gog-state" "$BASELINE_DIR/memory" "$BASELINE_DIR/workspace-files"
cp "$GOG_STATE/emails.json" "$BASELINE_DIR/gog-state/" 2>/dev/null || true
cp "$GOG_STATE/calendar.json" "$BASELINE_DIR/gog-state/" 2>/dev/null || true
cp "$GOG_STATE/tasks.json" "$BASELINE_DIR/gog-state/" 2>/dev/null || true
cp "$GOG_STATE/sent.json" "$BASELINE_DIR/gog-state/" 2>/dev/null || true
cp "$GOG_STATE/auth.json" "$BASELINE_DIR/gog-state/" 2>/dev/null || true
cp "$GOG_STATE/contacts.json" "$BASELINE_DIR/gog-state/" 2>/dev/null || true
cp "$GOG_STATE/tasklists.json" "$BASELINE_DIR/gog-state/" 2>/dev/null || true
cp "$HOME/.openclaw/workspace/memory/"*.md "$BASELINE_DIR/memory/" 2>/dev/null || true
cp "$HOME/.openclaw/workspace/AGENTS.md" "$BASELINE_DIR/workspace-files/" 2>/dev/null || true
cp "$HOME/.openclaw/workspace/SOUL.md" "$BASELINE_DIR/workspace-files/" 2>/dev/null || true
cp "$HOME/.openclaw/workspace/TOOLS.md" "$BASELINE_DIR/workspace-files/" 2>/dev/null || true
cp "$HOME/.openclaw/workspace/USER.md" "$BASELINE_DIR/workspace-files/" 2>/dev/null || true
cp "$HOME/.openclaw/workspace/IDENTITY.md" "$BASELINE_DIR/workspace-files/" 2>/dev/null || true
cp "$HOME/.openclaw/openclaw.json" "$BASELINE_DIR/" 2>/dev/null || true
echo "📸 Baseline snapshot saved to $BASELINE_DIR"

# Export for Python subprocess
SEED_SCRIPT="$SKILL_DIR/scripts/seed-mock-gog-state.py"

export MODEL_NAME TASK_FILTER RESULTS_DIR DISPATCH GOG_STATE SESSIONS_DIR MEMORY_DIR SKILL_DIR SEED_SCRIPT
export TASKS_FILE ARCHIVE_DIR RUN_ID RUN_DATE

# Load tasks
if [ ! -f "$TASKS_FILE" ]; then
  echo "❌ tasks.json not found at $TASKS_FILE"
  exit 1
fi

echo "========================================"
echo "🗡️  Jake Benchmark — $MODEL_NAME"
echo "Started: $(date)"
echo "========================================"

# Parse tasks and run each
# -u makes Python unbuffered so logs stream during long runs.
python3 -u << 'PYEOF'
import json, subprocess, os, time, shutil, sys

tasks_file = os.environ.get("TASKS_FILE", os.path.expanduser("~/skills/jake-benchmark/tasks.json"))
results_dir = os.environ.get("RESULTS_DIR", "/tmp/results")
dispatch = os.environ.get("DISPATCH", os.path.expanduser("~/scripts/jake-dispatch.py"))
gog_state = os.environ.get("GOG_STATE", os.path.expanduser("~/.config/gogcli/state"))
sessions_dir = os.environ.get("SESSIONS_DIR", os.path.expanduser("~/.openclaw/agents/main/sessions"))
memory_dir = os.environ.get("MEMORY_DIR", os.path.expanduser("~/.openclaw/workspace/memory"))
archive_dir = os.environ.get("ARCHIVE_DIR", "/tmp/archive")
run_id = os.environ.get("RUN_ID", "unknown")
run_date = os.environ.get("RUN_DATE", "unknown")
task_filter = os.environ.get("TASK_FILTER", "")
model_name = os.environ.get("MODEL_NAME", "unknown")

with open(tasks_file) as f:
    tasks = json.load(f)

# Optional tasks (do not change the historical 22-task baseline unless explicitly enabled)
include_experimental = os.environ.get("JAKE_INCLUDE_EXPERIMENTAL", "0").strip() in {"1", "true", "yes"}
if not include_experimental:
    tasks = [t for t in tasks if not t.get("experimental")]

# Filter tasks if requested
if task_filter:
    tasks = [t for t in tasks if task_filter.lower() in t["id"].lower() 
             or task_filter.lower() in t["difficulty"].lower()
             or task_filter.lower() in t["name"].lower()]

total = len(tasks)
print(f"\n📋 Running {total} tasks for model: {model_name}\n")

# Timeout per difficulty (generous — local models need time, especially with thinking enabled)
timeouts = {"medium": 900, "hard": 1200, "very_hard": 1800}

# Consecutive-failure abort: if N tasks in a row produce 0 responses AND 0 tool calls
# AND 0 total entries, the model is clearly broken. Abort early to save hours.
MAX_CONSECUTIVE_DEAD = int(os.environ.get("JAKE_MAX_CONSECUTIVE_DEAD", 3))
consecutive_dead = 0

for i, task in enumerate(tasks):
    task_id = task["id"]
    difficulty = task["difficulty"]
    timeout = timeouts.get(difficulty, 300)
    # Unique per run so concurrent/stale dispatchers never collide.
    session_id = f"bench-{run_date}-{task_id}"
    task_dir = os.path.join(results_dir, task_id)
    
    print('\n' + '=' * 50)
    print(f"[{i+1}/{total}] {task['name']} ({difficulty})")
    print('=' * 50)
    
    # --- KEEPALIVE: ping Ollama to prevent model eviction between tasks ---
    # The model can be evicted during long benchmark runs if keep_alive expires.
    # Send a trivial request with keep_alive=6h to reset the timer.
    try:
        import urllib.request
        keepalive_data = json.dumps({
            "model": model_name.replace("ollama/", ""),
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False, "keep_alive": "6h",
            "options": {"num_predict": 1}
        }).encode()
        req = urllib.request.Request(
            "http://100.69.102.71:11434/api/chat",
            data=keepalive_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=120)
        print("  🔄 Model keepalive OK")
    except Exception as e:
        print(f"  ⚠️ Model keepalive failed: {e}")

    # --- RESET STATE ---
    print("  Resetting state...")
    
    # Seed stable mock gog fixture state (emails + calendar + contacts + etc)
    seed_script = os.environ.get("SEED_SCRIPT", "")
    if seed_script and os.path.exists(seed_script):
        try:
            out = subprocess.run(
                ["python3", seed_script],
                capture_output=True,
                text=True,
                timeout=25,
            )
            if out.returncode != 0:
                print(f"  ⚠️ Fixture seed failed (rc={out.returncode}): {out.stderr.strip()[:200]}")
            else:
                print("  🧪 Fixture seed OK")
        except Exception as e:
            print(f"  ⚠️ Fixture seed exception: {e}")
    else:
        print("  ⚠️ Fixture seed script missing, continuing with existing gog state")
    
    # Clear memory files
    if os.path.exists(memory_dir):
        for f_name in os.listdir(memory_dir):
            if f_name.endswith(".md"):
                os.remove(os.path.join(memory_dir, f_name))
    
    # Clear old benchmark sessions
    for f_name in os.listdir(sessions_dir):
        if f_name.startswith("bench-"):
            os.remove(os.path.join(sessions_dir, f_name))
    
    # Handle error injection for error_recovery task
    env_extra = {}
    if task_id == "error_recovery":
        env_extra["GOG_INJECT_ERROR"] = "1"
        print("  ⚡ Error injection enabled")
    
    # Reset test site
    try:
        subprocess.run(["curl", "-s", "-X", "POST", "http://127.0.0.1:3456/test/reset"], 
                       capture_output=True, timeout=5)
    except:
        pass
    
    # --- DISPATCH TASK ---
    print(f"  Sending: {task['prompt'][:80]}...")
    
    env = os.environ.copy()
    env.update(env_extra)
    
    result = subprocess.run(
        ["python3", dispatch, task["prompt"], session_id, str(timeout)],
        capture_output=True, text=True, timeout=timeout + 60, env=env
    )
    
    print(f"  Dispatch output: {result.stdout.strip()}")
    if result.stderr:
        print(f"  Stderr: {result.stderr.strip()[:200]}")
    
    # --- COLLECT ARTIFACTS ---
    print("  Collecting artifacts...")
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(os.path.join(task_dir, "gog-state"), exist_ok=True)
    os.makedirs(os.path.join(task_dir, "memory"), exist_ok=True)
    
    resp_data = {}
    # Response JSON from dispatch
    response_file = f"/tmp/jake-response-{session_id}.json"
    if os.path.exists(response_file):
        shutil.copy2(response_file, os.path.join(task_dir, "response.json"))
        with open(response_file) as f:
            resp_data = json.load(f)
        # Also write plain response text (prefer final/response, fall back to last responses[])
        resp_text = (
            resp_data.get("final_response")
            or resp_data.get("response")
            or ((resp_data.get("responses") or resp_data.get("all_responses") or [])[-1] if (resp_data.get("responses") or resp_data.get("all_responses")) else "")
        )
        if isinstance(resp_text, str) and resp_text.strip():
            with open(os.path.join(task_dir, "response.txt"), "w") as f:
                f.write(resp_text)
    
    # Session JSONL (OpenClaw stores logs by session UUID, not the explicit session id)
    session_jsonl_path = resp_data.get("session_jsonl_path")
    if session_jsonl_path and os.path.exists(session_jsonl_path):
        shutil.copy2(session_jsonl_path, os.path.join(task_dir, "session.jsonl"))
    
    # Gog state
    for f_name in ["calendar.json", "tasks.json", "sent.json"]:
        src = os.path.join(gog_state, f_name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(task_dir, "gog-state", f_name))
    
    # Memory files
    if os.path.exists(memory_dir):
        for f_name in os.listdir(memory_dir):
            if f_name.endswith(".md"):
                shutil.copy2(
                    os.path.join(memory_dir, f_name),
                    os.path.join(task_dir, "memory", f_name)
                )
    
    # Test site results
    try:
        result = subprocess.run(
            ["curl", "-s", "http://127.0.0.1:3456/test/results"],
            capture_output=True, text=True, timeout=5
        )
        with open(os.path.join(task_dir, "test-results.json"), "w") as f:
            f.write(result.stdout)
    except:
        pass
    
    # Metadata
    with open(os.path.join(task_dir, "metadata.json"), "w") as f:
        json.dump({
            "task_id": task_id,
            "task_name": task["name"],
            "difficulty": difficulty,
            "prompt": task["prompt"],
            "model": model_name,
            "elapsed_seconds": resp_data.get("elapsed_seconds"),
            "status": resp_data.get("status", "unknown"),
            "tool_call_count": len(resp_data.get("tool_calls", [])),
            "response_count": len(resp_data.get("responses", resp_data.get("all_responses", []))),
            "timestamp": resp_data.get("finished", ""),
        }, f, indent=2)
    
    # Archive: copy entire task_dir to dated archive
    archive_task_dir = os.path.join(archive_dir, task_id)
    if os.path.exists(task_dir):
        shutil.copytree(task_dir, archive_task_dir, dirs_exist_ok=True)
    
    # Brief status (no evaluation)
    elapsed = resp_data.get("elapsed_seconds", "?")
    tools = len(resp_data.get("tool_calls", []))
    responses = len(resp_data.get("responses", resp_data.get("all_responses", [])))
    compacted = resp_data.get("has_compaction", False)
    total_entries = resp_data.get("total_entries", 0)
    print(f"  📦 Collected in {elapsed}s — {responses} responses, {tools} tool calls, {total_entries} entries, compaction: {compacted}")

    # Consecutive-failure tracking
    if responses == 0 and tools == 0 and total_entries == 0:
        consecutive_dead += 1
        if consecutive_dead >= MAX_CONSECUTIVE_DEAD:
            print(f"\n❌ ABORT: {consecutive_dead} consecutive tasks with zero activity.")
            print(f"   Model '{model_name}' appears non-functional through the gateway.")
            print(f"   Completed {i+1}/{total} tasks before aborting.")
            break
    else:
        consecutive_dead = 0

# Write run manifest to archive
with open(os.path.join(archive_dir, "manifest.json"), "w") as f:
    json.dump({
        "run_id": run_id,
        "run_date": run_date,
        "model": model_name,
        "thinking_level": os.environ.get("THINKING_LEVEL", "off"),
        "tasks_run": len(tasks),
        "task_ids": [t["id"] for t in tasks],
        "filter": task_filter or None,
        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, f, indent=2)

print('\n' + '=' * 50)
print(f"🏁 Benchmark complete!")
print(f"Results (latest): {results_dir}")
print(f"Archive (permanent): {archive_dir}")
print(f"Finished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print('=' * 50)
PYEOF
