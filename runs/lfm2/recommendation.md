# lfm2 - Recommendation

## Do NOT use for agent work.

lfm2 scores 15/508 (3.0%), placing it 5th of 7 models tested. It is not viable for any agent task that requires tool calling, file operations, or API interaction.

### Strengths
- Solid safety/security instincts (best phishing refusal in the small-model category)
- Good persona/character work when it does respond
- Persistent problem-solving attempts (34 tool calls on browser task)

### Weaknesses
- Cannot reliably discover or call tools (gog, browser MCP)
- Hallucinates file paths, fake emails, and task completion
- 9 of 22 tasks produced zero output
- Zero artifacts created across all 22 tasks

### When to consider lfm2
- Never, for agent work on OpenClaw
- Potentially useful for pure language tasks (summarization, writing) where no tool calling is required
- The safety training is genuinely good and could be valuable in security-sensitive chat contexts

### Better alternatives
- qwen3.5:27b-q4_K_M (250 points, 17x better)
- qwen3.5:35b (118 points, 8x better)
- qwen3:8b (24 points, 1.6x better, similar size class)
