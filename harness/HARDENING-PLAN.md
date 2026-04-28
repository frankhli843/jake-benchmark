# Jake Benchmark Hardening Plan v1

Status: foundation landed on branch `harden/foundation-v1`. Follow-up subtasks remain.

## 1. Repo + open-source readiness decision

Three repos are involved today:

- `jake-benchmark` (this repo): public-facing benchmark site, run artifacts, dashboard, the `harness/` scripts that drive a Pi.
- `gemmaclaw` `src/gemmaclaw/benchmark-kit`: TypeScript shared core. Already exposes a task pack format, sweep runner, config selection algorithm, and an `anonymize`/`upload` flow.
- The Pi-side `frankclaw` gateway: required at runtime; not a benchmark dependency.

Decision (this plan):

- **`gemmaclaw/benchmark-kit` is the canonical shared core.** All schemas (task pack, run artifact, failure report) live there as JSON Schema files plus TypeScript types. Runners, sanitizers, and the report generator are libraries inside benchmark-kit. CLI surface is `gemmaclaw benchmark` (existing) plus a thin runner adapter.
- **`jake-benchmark` is a benchmark content repo plus a deployment glue layer.** It owns: `harness/tasks.json` (now wrapped as a versioned pack), `harness/scripts/run-model-benchmark.sh` (the orchestrator), the dashboard at `/index.html`, and the `runs/` artifacts. Anything reusable across multiple benchmarks gets pushed up to benchmark-kit.
- **No third repo.** Adding a fourth repo just to host the harness adds coordination cost without solving the real problem (interface boundaries). The boundary is enforced by package surface in benchmark-kit, not repo split.
- **License + audience.** jake-benchmark stays Apache-2.0 (matches gemmaclaw). Audience: model authors and infra engineers who want to reproduce the benchmark, see failure reports, or compare models.
- **Public-safe data policy.** No private hostnames, IPs, file paths, email addresses, phone numbers, or identifiers in the public repo. Mock fixtures in `seed-mock-gog-state.py` are already synthetic; we add a redaction test that fails on known internal patterns. Reports are sanitized at generation time, not at upload time.

Why not pull jake into gemmaclaw entirely: dashboard + GitHub Pages site has its own deployment, and jake's task pack is heavily agent-centric (gateway integration, mock email/calendar fixtures) which would dilute benchmark-kit's tool-free model focus.

## 2. Versioned schemas (v1)

Three artifacts now have JSON Schemas under `harness/schemas/`:

- `task-pack-v1.schema.json`: pack metadata + tasks. Supports both jake-style agent grading (`output_check`, `multi_check`, `artifact_check`, `file_check`, `command_check`, `security_check`, `error_check`) and benchmark-kit-style direct grading (`exact_match`, `contains_all`, `json_structure`, `output_quality`). Versioned by a top-level `schemaVersion: "1"`.
- `run-artifact-v1.schema.json`: complete run directory descriptor. Captures benchmark version, runner version, model spec, hardware, environment, per-task results, summary, and a config hash. Stable across runners.
- `failure-report-v1.schema.json`: shareable failure summary. Per-task failure category, transcript excerpts (with character offsets into the canonical transcript), repro commands, and an environment block.

The migration tool `harness/scripts/migrate-tasks-to-v1.py` deterministically wraps the existing bare-list `tasks.json` into a v1 pack. Validation runs `jsonschema` against the schemas; the harness CLI fails fast on schema errors.

Why a wrapping migration rather than a full reformat: the existing grading types are useful and battle-tested. Forcing a translation to benchmark-kit's tool-free types would break the agent-loop tests that depend on `multi_check`. The schema admits both grading "dialects" by `family` discriminator: `family: "agent"` for jake-style or `family: "tool-free"` for benchmark-kit-style. Each `grading.type` is constrained by the family, and the runner picks a grader implementation by family.

## 3. Benchmark CLI entrypoint (docker-friendly)

Status: scaffolded, not finished. The current orchestrator (`run-model-benchmark.sh`) stays as the operational driver because it is what Frank uses today. New CLI surface:

```
jake-bench validate <pack.json>       # schema validation, exit non-zero on errors
jake-bench migrate <legacy.json>      # bare-list -> v1 pack
jake-bench report <run-dir>           # generate report.md + report.json
jake-bench report <run-dir> --sanitize public   # redact private patterns
jake-bench compare <run-dir-a> <run-dir-b>      # diff two runs
```

CLI lives at `harness/lib/cli.py`. The full `run` subcommand depends on the runner abstraction (subtask 4) and is not landed in this foundation. Existing scripts are unchanged.

## 4. Runner abstraction + OpenClaw adapter (no global state mutation)

Status: design only in this foundation. The proposed interface:

```python
class Runner(Protocol):
    name: str            # "openclaw", "mock", "gemmaclaw"
    version: str
    def prepare(self, model_spec: ModelSpec, workspace: Path) -> RunContext: ...
    def execute(self, task: Task, ctx: RunContext) -> TaskResult: ...
    def teardown(self, ctx: RunContext) -> None: ...
```

`RunContext` is a per-run scratch directory. The OpenClaw adapter takes a config overlay path rather than mutating `~/.openclaw/openclaw.json`. It launches the gateway with `OPENCLAW_CONFIG=<overlay>` (verified to be supported in the gateway today), so per-run state isolation is real and reversible. It does not delete `workspace/MEMORY.md` or `workspace/memory/*.md` from the user's home; it points the gateway at a temp workspace seeded from a pristine snapshot.

Implementing this requires changes to the orchestrator script and a small change in frankclaw to honor `OPENCLAW_CONFIG` if it does not already. That is a separate, larger subtask.

A `MockRunner` is included in this foundation (`harness/lib/mock_runner.py`) so the CLI smoke test can run without GPU hardware. It returns deterministic synthetic transcripts and synthetic failures.

## 5. Model and checkpoint spec (matrix runs)

Format (`harness/schemas/model-spec-v1.schema.json` planned):

```yaml
provider: ollama
model_id: qwen3.5:27b
checkpoint_id: q4_K_M
options:
  context_length: 32768
  thinking: medium
hardware:
  gpu: rtx-3090
  vram_gb: 24
```

Matrix runs are a list of `ModelSpec`s. The runner records the full spec and a `config_hash` (sha256 over the canonical JSON of the spec) on every run artifact. The hash is the join key for cross-run comparison. This work depends on the runner abstraction landing first.

## 6. Shareable failure reports

Generator at `harness/lib/failure_report.py`. Takes a v1 run artifact and emits:

- `report.md`: human-readable summary, one section per failed task, executive summary at top.
- `report.json`: same content machine-readable, conforming to `failure-report-v1.schema.json`.

Each failed task carries:

- Task id + category + max score + actual score
- A failure category from a small taxonomy: `tool_call_missing`, `tool_call_wrong`, `output_format`, `factual_error`, `safety_violation`, `infra_error`, `judge_error`, `other`
- A transcript excerpt with offsets into the canonical transcript (excerpt length capped at 2 KB)
- A minimal repro command pointer (model spec + task id) so a reader can re-run just that task

The report is self-contained: a reader does not need the dashboard.

## 7. Sanitization / public-safe mode

Library at `harness/lib/sanitize.py` plus tests at `harness/lib/tests/test_sanitize.py`. Redacts:

- IPv4 + IPv6 addresses (incl. Tailscale 100.64.0.0/10 range, called out explicitly)
- Email addresses
- Absolute paths under `/home/`, `/Users/`, `/root/`, `/var/lib/`
- Known internal hostnames (frank-pc, frankpi, frank-wsl, clawed-*)
- Phone numbers (E.164 + North American)
- Bearer tokens, OAuth refresh tokens, AWS keys, Anthropic + OpenAI key prefixes

Tests run a corpus of "should redact" and "should pass" fixtures plus a final regex sweep that fails the build if a known sensitive pattern survives the pipeline. Golden fixture corpus is in `harness/lib/fixtures/`.

## 8. CI: unit tests + smoke benchmark with MockRunner

Foundation: pytest config in `harness/pyproject.toml` (planned, not landed in this slice). Unit tests landed for sanitizer + report generator + migration tool. Smoke benchmark CLI exists via `MockRunner`; wiring into a GitHub Actions workflow is a follow-up.

The smoke workflow shape: install python deps, run `jake-bench validate harness/tasks-pack.json`, run `jake-bench` against `MockRunner` for 2 tasks, assert `run.json` validates, assert `report.md` and `report.json` are generated and pass redaction.

## 9. Docs + summary artifact

This document plus per-script READMEs and the existing `harness/README.md` cover the agent-readable docs. A polished Google Doc summary with screenshots of generated reports and dashboard integration is part of the wrap-up subtask after the rest land.

## What's done in this foundation

- Versioned JSON Schemas for task pack, run artifact, failure report
- Migration tool: bare-list tasks.json -> v1 pack (deterministic, idempotent)
- Failure report generator (markdown + JSON)
- Sanitization library with golden fixture tests
- MockRunner for hardware-free smoke testing
- This design note

## Follow-up subtasks (next worker runs)

1. **Runner abstraction + OpenClaw adapter** that uses `OPENCLAW_CONFIG` overlay rather than mutating user-global config.
2. **Full CLI** (`jake-bench run`) wired through the runner interface.
3. **Matrix runs** consuming `ModelSpec` lists, with config-hash join keys.
4. **CI workflow** running smoke benchmark + redaction tests on every PR.
5. **Polished public-facing docs** (Google Doc) once the runner abstraction lands.

Each is a clean, independently-shippable scope and should be its own todo with its own CC ACP worker.
