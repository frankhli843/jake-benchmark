# qwen3.6:35b-a3b-q4_K_M at High Thinking - Commentary

## The Numbers

| Metric | Value |
|--------|-------|
| Score | 110/508 |
| Percentage | 21.7% |
| Tier | B |
| Tasks with any output | 14/23 |
| Perfect scores | 5/23 |
| Tasks with 0 tool calls | 9/23 |
| Most tool calls in one task | 13 (conditional_logic) |

## The Story

The quantized Qwen 3.6 is a split personality. On its good days, it's arguably the most polished agent in the benchmark. On its bad days, it stares at the wall for 8 minutes and produces nothing. And on its weird days, it confidently executes the wrong task entirely.

The medium tier tells the success story: 53/53, a perfect sweep. Every email read, every task created, every file written. The email_summarize response was genuinely impressive: phishing detected, urgency categorized, actionable recommendations for each email. The calendar_create response had personality ("You good, bro?"). This model has charm.

Then the hard tier breaks the spell. pb_meetings worked (mostly), but finn_quests and lady_party: total silence. The model can schedule Princess Bubblegum's lab reviews but can't handle Finn's quests, a task with nearly identical structure. The inconsistency is the defining feature.

## The Task Confusion Bug

The strangest failure is partial_error_recovery. Asked to send 3 emails (with the first one deliberately failing), the model instead checked the calendar and wrote a data reconciliation report. It produced a DIFFERENT task's deliverable, and it did it well. The reconciliation report was structured, comprehensive, with proper tables and source attribution. The model is capable and confused.

This suggests that at high thinking, the model's extended deliberation occasionally causes it to lose track of what it was asked to do. It's reading its own context, finding task-like patterns, and executing whichever one resonates with its current thinking state rather than the actual prompt.

## The 4th Wall Break

In email_triage, the model recognized the test data: "These aren't real emails, they're test data (Adventure Time themed, with fake addresses)." It then adjusted all urgency ratings downward based on the data being fake. This is simultaneously smart and wrong. The benchmark expects the model to role-play within the test scenario. A model that breaks character loses points.

## The Reading-Without-Writing Problem

conditional_logic used 13 tool calls, the most of any task. But ZERO of those calls were writes. The model read Finn's email, checked the calendar, listed tasks, cross-referenced dates, for 776 seconds. Then its final response was: "Okay, let me read Finn's quest email and check your calendar." It had already done that 13 times. This is high-thinking analysis paralysis in its purest form: the model gathers information endlessly but never transitions to action.

## Where It Sits

At 110/508 (21.7%), qwen3.6:35b-a3b-q4_K_M at high thinking matches qwen3.5:35b at off thinking (118/508, 23.2%). It sits firmly in B tier: capable of real agent work on straightforward tasks, but unreliable for anything beyond medium complexity.

Compared to its full-precision counterpart (qwen3.6:35b at 35/508), the quantized variant is 3.1x better. The quantization advantage continues to be the strongest signal in the benchmark.

## Key Findings for Qwen 3.6

1. **Quantization matters more than ever.** 3.1x score difference between full and quantized at the same thinking level.
2. **Perfect medium tier.** First model since qwen3.5:27b-q4_K_M/medium to sweep all 5 medium tasks.
3. **High thinking is still too much.** The thinking paralysis pattern persists from qwen3.5. Medium thinking likely the sweet spot here too.
4. **Task confusion is new.** The partial_error_recovery bug (executing wrong task) is a novel failure mode not seen in qwen3.5 models.
5. **4th wall awareness.** The model's meta-awareness of test data is a double-edged sword.

## Verdict

Promising but needs medium/low thinking testing. The perfect medium tier suggests real agent potential if the thinking budget is reined in. At high thinking, it's a B-tier model with an inconsistency problem. The next benchmark round should test this model at all thinking levels to find its sweet spot.
