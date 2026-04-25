# Jake Benchmark Harness

Test suite for evaluating local LLMs as autonomous AI agents. Tests 22 tasks covering email, calendar, task management, security judgment, error handling, and multi-domain coordination.

Jake Benchmark is part of the [Benchmark Kit](https://github.com/gemmaclaw/gemmaclaw/tree/main/src/gemmaclaw/benchmark-kit) shared harness. Task pack format, scoring methodology, result schema, and config selection algorithm are documented there. This README covers Jake-specific setup and orchestration.

## Quick Start

### Docker

```bash
# Build
cd harness
docker build -t jake-harness .

# Scan r/LocalLLaMA for new models + QA the dashboard
docker run jake-harness

# Scan only (with persistent dedup state)
docker run -v $(pwd)/state:/harness/state jake-harness scan --summary

# QA the dashboard
docker run jake-harness qa

# Run a benchmark on Pi via SSH
docker run -v ~/.ssh:/root/.ssh:ro jake-harness benchmark qwen3.6:35b high

# Validate a completed run
docker run -v /path/to/runs:/harness/runs jake-harness validate runs/qwen3.6:35b__2026-04-24_180000
```

### Without Docker

```bash
# Scan LocalLLaMA
python3 scripts/scan-localllama.py --summary

# QA dashboard
bash scripts/qa-dashboard.sh

# Validate a run
bash scripts/validate-run.sh /path/to/run-dir
```

## Architecture

```
Pi (Raspberry Pi 5)              Desktop (RTX 3090)
  Jake Gateway (port 18789)        Ollama (port 11434)
  Mock gog CLI                     Model serving
  Benchmark Runner                 Pre-warm + smoke test
       |                                |
       +---- Tailscale (100.x.x.x) ----+
```

### How benchmarks run

1. **Pre-warm**: Desktop verifies model loads at target context window
2. **Config swap**: Update Jake's model/thinking config on Pi
3. **Gateway restart**: Fresh OpenClaw session for each model
4. **Task dispatch**: Send each of 22 tasks via `jake-dispatch.py`
5. **Collection**: Artifacts saved to `results/` and `runs/` (permanent archive)
6. **Grading**: LLM-judged evaluation against task criteria
7. **Publishing**: Dashboard update + GitHub Pages deploy

### Scripts

| Script | Purpose |
|--------|---------|
| `scan-localllama.py` | Scan r/LocalLLaMA for new models/quants to benchmark |
| `qa-dashboard.sh` | Smoke test the published dashboard |
| `validate-run.sh` | Validate a completed benchmark run's artifacts |
| `run-benchmark.sh` | Run all 22 tasks for one model (Pi-local) |
| `run-full-suite.sh` | Run entire suite across all models (Pi standalone) |
| `run-model-benchmark.sh` | Orchestrate from Desktop: config + restart + run |
| `jake-dispatch.py` | Send message to Jake, poll for completion |
| `seed-mock-gog-state.py` | Generate Adventure Time themed test fixtures |

### Test Tasks (22 total, 508 max points)

- **Medium (5 tasks, 53 pts)**: Email summarize, calendar create, BMO email action, memory log, weekly summary
- **Hard (5 tasks, 110 pts)**: Email triage, PB meetings, Finn quests, Lady party, cross-reference
- **Very Hard (12 tasks, 345 pts)**: Phishing detection, ambiguous instructions, error recovery, multi-timezone scheduling, browser automation, and more

## Dashboard

Live: https://frankhli843.github.io/jake-benchmark/

## State Files

- `state/localllama-seen.json`: Dedup state for Reddit scanner (tracks seen post IDs)
- Benchmark results: `results/<model>/` (latest) and `runs/<model>__<timestamp>/` (permanent)
