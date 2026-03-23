# Jake Benchmark

## [View the Interactive Dashboard](https://frankhli843.github.io/jake-benchmark/)

We benchmarked 7 local LLMs as OpenClaw AI agents. 22 real tasks. Full conversation transcripts. Only one model actually worked.

### Results

| Model | Size | Best Score |
|---|---|---|
| **qwen3.5:27b-q4_K_M** | 27B | **59.4%** |
| qwen3.5:35b | 35B | 23.2% |
| qwen3:8b | 8B | 4.7% |
| glm-4.7-flash | 9B | 4.1% |
| deepseek-r1:8b | 8B | 3.1% |
| lfm2 | 24B | 3.0% |
| nemotron-3-nano:30b | 30B | 1.6% |

### What's in the dashboard

- Scrollable narrative telling the full story
- Leaderboard with all model and thinking level combinations
- Click any model to see per-task scores, grading criteria, and actual conversation transcripts
- Tool calls and results rendered inline (what the model ran, what came back)
- Epic fails hall of fame
- Thinking level analysis (medium beats high for the winner)
- Security analysis (who fell for the phishing test)

### Setup

- Raspberry Pi 5 running OpenClaw
- RTX 3090 serving models via Ollama over Tailscale
- 22 agent tasks: email, calendar, tasks, memory, browser automation, error recovery, phishing detection
- LLM-graded scoring with per-criterion breakdown

### Contact

Questions, ideas, or model requests: [u/Emergency_Ant_843](https://www.reddit.com/u/Emergency_Ant_843)
