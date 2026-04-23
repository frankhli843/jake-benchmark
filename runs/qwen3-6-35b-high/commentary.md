# qwen3.6:35b at High Thinking - Commentary

## The Numbers

| Metric | Value |
|--------|-------|
| Score | 35/508 |
| Percentage | 6.9% |
| Tier | D |
| Tasks with any output | 3/23 |
| Tasks with 0 tool calls | 20/23 |
| Avg elapsed per silent task | ~470s |

## The Story

qwen3.6:35b at high thinking is a cautionary tale about the relationship between model size, thinking budget, and actual productivity. This is a 35-billion parameter model given maximum thinking time, and it produced absolutely nothing for 87% of its tasks.

The three tasks that DID work tell an interesting story:

1. **calendar_create (10/10)** - Perfect execution. Correct date math, event creation, all details included.
2. **email_act_bmo (15/15)** - Perfect execution. Read email, extracted items, created 5 prioritized tasks.
3. **email_summarize (2/10)** - Found gog, listed emails, but response was thinking text, not a summary.

Two out of three working tasks scored perfectly. The model CAN do agent work. But high thinking budget turns it into a statue for everything else. 470 seconds of GPU time per task, producing exactly zero tokens of output.

## The Thinking Paralysis Pattern

Every failed task follows the same signature:
- `tool_call_count: 0`
- `response_count: 0`
- `elapsed_seconds: ~470`

The model spends the entire timeout window in its thinking loop, never bridging from thought to action. This isn't a tool-discovery problem (it found gog fine) or a capability problem (it created tasks perfectly). It's a pure analysis-paralysis problem where high thinking budget gives the model too much room to deliberate.

## Comparison Context

At 35/508 (6.9%), qwen3.6:35b sits between glm-4.7-flash (21/508, 4.1%) and the qwen3:8b models (16-24/508). But those models are 5-10x smaller. A 35B model scoring at the level of 8B models is embarrassing, especially when the task failures are due to producing ZERO output rather than wrong output.

## The Quantization Paradox Continues

The qwen3.5 generation proved that quantized 27B beats full 35B. Now qwen3.6 repeats the pattern: the a3b quantized variant (110/508, 21.7%) outscores the full 35B (35/508, 6.9%) by 3.1x at the same thinking level. Quantization isn't just "acceptable quality loss" - it's actively better for agent work, probably because the smaller memory footprint allows faster inference and earlier escape from thinking loops.

## Verdict

Do not use qwen3.6:35b at high thinking for agent work. The model has genuine capability (proven by its perfect scores on 2 tasks), but high thinking turns that capability into a liability. Test at medium or low thinking before writing it off entirely.
