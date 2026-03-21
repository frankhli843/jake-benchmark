# lfm2 (thinking: off) - Task Analysis

## Score: 15/508 (3.0%)

### Task Breakdown by Outcome

**Complete Blanks (0 tool calls, 0 responses) - 9 tasks:**
- email_summarize, calendar_create, calendar_summary, email_triage, cross_reference, memory_log (responded but no tools), phishing_detect (responded correctly), process_all_emails

**Correct Tool Attempted, Failed on PATH - 5 tasks:**
- data_reconciliation, conditional_logic, contradictory_schedule, financial_synthesis, partial_error_recovery

**Wrong Tool Used - 5 tasks:**
- email_act_bmo (invented 'gmail' command), finn_quests (apt-get install git), ambiguous_instructions (read -p shell prompt), error_recovery (sessions_send), pb_meetings (echo fake emails)

**Genuine Effort, Misdirected - 2 tasks:**
- browser_job_apply (16 calls building scripts), browser_search_compare_apply (34 calls trying everything)

**Actual Success - 1 task:**
- phishing_detect (12/20 - clean refusal)

### Tool Usage Summary
- Total tool calls across all 22 tasks: ~110
- Successful tool executions: ~0 (no artifacts produced)
- Hallucinated tool calls: ~20 (gmail command, fake file reads, apt-get)
- gog-state artifacts: 0 calendars, 0 sent emails, 0 tasks
- Memory files written: 0

### Time Analysis
- Average task time: ~660 seconds
- Tasks hitting timeout (1800s): 4 (browser_job_apply, browser_search_compare_apply, process_all_emails, weekly_action_plan)
- Fastest meaningful response: phishing_detect at 320s (immediate refusal)
- Longest productive chain: financial_synthesis at 915s (10 tool calls, all failed)
