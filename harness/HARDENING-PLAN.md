# Jake Benchmark Hardening Plan v1

Status:
- Foundation landed via PR #3 (commit `5cb152c` on `main`).
- Milestone 2 (runner abstraction + matrix + CLI run/compare) lands via the
  `harden/milestone-2-runner-matrix` branch. Polished Google Doc summary follows.

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

Status: complete (milestone 2). The current orchestrator
(`run-model-benchmark.sh`) stays as the operational driver because it is what
Frank uses today, but the new `jake-bench` CLI is the contract everything else
talks to. CLI lives at `harness/lib/cli.py`. Subcommands:

```
jake-bench validate <schema> <file>             # task-pack-v1 / run-artifact-v1 / failure-report-v1
jake-bench migrate <legacy.json> <out.json>     # bare-list -> v1 pack
jake-bench report <run-dir> [--sanitize public] # generate report.md + report.json
jake-bench smoke <pack.json> --out <dir>        # run pack against MockRunner
jake-bench sanitize <file> [--profile public]   # apply redaction
jake-bench run --pack <p> --out <dir> \         # one ModelSpec via runner abstraction
              --runner mock|openclaw \
              --spec provider:model_id \
              [--checkpoint X] [--thinking off|low|medium|high] \
              [--baseline-config <path>] [--baseline-home <path>] \
              [--dispatch-cmd ...]
jake-bench matrix --pack <p> --out <dir> \      # run a matrix file (or single spec)
              --matrix <matrix.json> --runner mock|openclaw
jake-bench compare <run-dir-a> <run-dir-b>      # diff two runs (regressions + improvements)
              [--out <dir>]
```

The CLI is wired into `harness/Dockerfile` via the `bench` entrypoint command:

```
docker run jake-harness bench validate task-pack-v1 tasks-pack-v1.json
docker run jake-harness bench smoke tasks-pack-v1.json --out /tmp/out
docker run jake-harness bench run --pack tasks-pack-v1.json --runner mock \
    --spec mock:smoke --out /tmp/out
```

Existing scripts are unchanged.

## 4. Runner abstraction + OpenClaw adapter (no global state mutation)

Status: complete (milestone 2). Lives in `harness/lib/runner.py`. The runtime
shape is:

```python
class Runner(Protocol):
    name: str
    version: str
    adapter: str
    def prepare(self, model_spec: ModelSpec, workspace: Path) -> RunContext: ...
    def run_pack(self, pack: dict, model_spec: ModelSpec, ctx: RunContext,
                 *, out_dir: Path) -> dict: ...
    def teardown(self, ctx: RunContext) -> None: ...
```

`RunContext` is a per-run scratch directory. Two concrete adapters:

- `MockRunner` — wraps `lib.mock_runner.run`. Used by CI smoke + dev workflows
  without GPU hardware. Restamps `runner.name=mock` on the artifact.
- `OpenclawRunner` — subprocess wrapper around `harness/scripts/run-model-benchmark.sh`
  (or any caller-supplied `dispatch_cmd`). Isolates per-run state by:
  1. Creating a scratch `OPENCLAW_HOME` directory under the per-run workspace.
  2. Optionally seeding it from a baseline `--baseline-home` snapshot
     (skips `node_modules`, `__pycache__`, `.git`, `logs`, `sessions`).
  3. Writing the per-run `model-spec.json` and `task-pack.json` into the
     scratch dir.
  4. Setting `OPENCLAW_HOME` and `OPENCLAW_CONFIG_PATH` env vars before
     invoking the dispatch command, so frankclaw resolves all state into the
     scratch tree (`resolveConfigDir` honors both env vars today;
     `src/utils.ts:138`). No mutation of `~/.openclaw/openclaw.json`,
     no deletion of `workspace/MEMORY.md`, no SSH side effects beyond
     what the dispatch command itself decides to do.
  5. After dispatch, the adapter reads `<out_dir>/run.json`, restamps the
     runner identity + hardware probe, and returns the artifact.

The factory `build_runner(kind, ...)` keeps callers free of import paths and
makes adding a third runner (e.g. `gemmaclaw`) a one-line registry change.

Tests cover the isolation contract end-to-end: a fake dispatch script asserts
that the real `~/.openclaw/openclaw.json` is untouched and that the scratch
home + config env vars are wired through (`harness/lib/tests/test_runner.py::
test_openclaw_runner_isolation`).

## 5. Model and checkpoint spec (matrix runs)

Status: complete (milestone 2). The `ModelSpec` shape is defined inline in the
existing `run-artifact-v1.schema.json` ($defs/ModelSpec) and mirrored as a
typed Python dataclass in `harness/lib/model_spec.py`. Adding a separate
`model-spec-v1.schema.json` would be redundant since the canonical home for
the shape is the run artifact.

Single spec example:

```json
{
  "provider": "ollama",
  "model_id": "qwen3.5:27b",
  "checkpoint_id": "q4_K_M",
  "options": {"context_length": 32768, "thinking": "medium"}
}
```

Matrix expansion (`lib.model_spec.expand_matrix` and `jake-bench matrix`):

```json
{
  "kind": "matrix",
  "provider": "ollama",
  "model_id": "qwen3.5:27b",
  "checkpoint_ids": ["q4_K_M", "q5_K_M"],
  "options": {"temperature": 0.0},
  "options_matrix": {
    "thinking": ["off", "low", "medium"],
    "context_length": [8192, 32768]
  }
}
```

The Cartesian product of `checkpoint_ids` x `options_matrix` (merged with the
fixed `options`) yields 12 specs. The matrix runner writes one
`<spec_slug>__<config_hash8>/run.json` per spec plus a top-level
`matrix.json` index that the dashboard / comparator can iterate.

The `config_hash` (sha256 over canonical JSON of the spec) is the join key
across runs and machines.

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

Status: complete. Workflow lives at `.github/workflows/harness-ci.yml`.

Coverage on every push / PR touching `harness/`:
- `pytest` over the full `harness/lib/tests/` suite (61 tests as of milestone 2).
- `jake-bench validate task-pack-v1` against the committed v1 pack.
- Migration idempotence check (regenerate the v1 pack from `tasks.json` and
  diff against the committed copy — fails if drift).
- `jake-bench smoke` end-to-end (MockRunner -> run.json -> report.md/json,
  schema-validate both).
- Redaction audit on the smoke report (regex sweep for emails, IPv4, home
  paths, anthropic key prefix). Fails the build on any leak that is not a
  `<REDACTED:...>` token.
- `jake-bench run` + `matrix` + `compare` integration smoke (added in
  milestone 2): run twice via MockRunner, expand a 4-spec matrix, compare
  the two identical runs and assert zero regressions.

The runtime is `python:3.11` on `ubuntu-latest` with a 10 minute timeout.
Total elapsed on PR #3 was 22 seconds.

## 9. Docs + summary artifact

This document plus per-script READMEs and the existing `harness/README.md` cover the agent-readable docs. A polished Google Doc summary with screenshots of generated reports and dashboard integration is part of the wrap-up subtask after the rest land.

## What's done

Foundation (PR #3, commit `5cb152c`):
- Versioned JSON Schemas for task pack, run artifact, failure report
- Migration tool: bare-list tasks.json -> v1 pack (deterministic, idempotent)
- Failure report generator (markdown + JSON)
- Sanitization library with golden fixture tests
- MockRunner for hardware-free smoke testing
- CI workflow with unit tests + smoke + redaction audit
- This design note

Milestone 2 (this branch):
- Runner abstraction (`lib/runner.py`) with `MockRunner` + `OpenclawRunner`
- ModelSpec dataclass + matrix expansion (`lib/model_spec.py`)
- Matrix executor + `matrix.json` index (`lib/matrix.py`)
- Compare report (regressions / improvements, JSON + Markdown) (`lib/compare.py`)
- Full `jake-bench` CLI: `run`, `matrix`, `compare` subcommands
- Docker `bench` entrypoint forwards directly to the python CLI
- CI extended to exercise run/matrix/compare against MockRunner

## Follow-up

These remain explicitly out of scope for milestone 2 because they require
live Pi hardware or larger product scope:

- Real OpenClaw `dispatch_cmd` running on Frank's Pi end-to-end against
  `qwen3.5:27b` to validate the OPENCLAW_CONFIG_PATH overlay path under real
  load. The adapter and isolation contract are tested in CI; the only
  remaining unknown is whether Pi-side Ollama/gateway interactions need any
  additional env wiring.
- Dashboard integration: read `matrix.json` and surface per-spec deltas in
  the existing `index.html`. Currently the dashboard reads per-model run
  dirs; the matrix index gives it a richer cross-checkpoint view.
- Move the schemas + runner Protocol into `gemmaclaw/benchmark-kit` so
  TypeScript consumers (e.g. the gemmaclaw CLI) can use the same contract.
  jake-benchmark stays the python implementation and the public benchmark
  content repo.
