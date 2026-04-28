"""Runner abstraction for the benchmark harness.

Defines the Protocol every runner adapter implements (mock, openclaw,
gemmaclaw). The harness CLI talks to runners through this surface
exclusively. No global filesystem mutation, no implicit Pi-only paths.

A `Runner` instance:
  * declares its name + version + adapter id (recorded in the run artifact)
  * receives a per-run scratch workspace (Path) it can write under freely
  * is given a `ModelSpec` and a v1 task pack
  * returns a v1-conformant run artifact

Concrete adapters:
  * `MockRunner`             - delegates to `lib.mock_runner.run`
  * `OpenclawRunner`         - subprocess wrapper around the existing
                                orchestrator, isolated via OPENCLAW_HOME +
                                OPENCLAW_CONFIG_PATH env overlay (no mutation
                                of `~/.openclaw/openclaw.json`).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import mock_runner
from .model_spec import ModelSpec


# --- Protocol surface --------------------------------------------------------


@dataclass
class RunContext:
    """Per-run scratch workspace. Created by the runner, owned by the runner."""

    workspace: Path
    started_at: _dt.datetime
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Runner(Protocol):
    """A runner adapter. Implementations MUST be free of global state mutation
    outside the supplied workspace path."""

    name: str
    version: str
    adapter: str

    def prepare(self, model_spec: ModelSpec, workspace: Path) -> RunContext: ...
    def run_pack(
        self, pack: dict, model_spec: ModelSpec, ctx: RunContext, *, out_dir: Path
    ) -> dict: ...
    def teardown(self, ctx: RunContext) -> None: ...


# --- Hardware probe ----------------------------------------------------------


def detect_hardware() -> dict:
    """Lightweight, no-network hardware probe. Best-effort; never raises."""

    info: dict[str, Any] = {}
    try:
        info["cpu"] = platform.processor() or platform.machine()
    except Exception:
        pass
    try:
        if hasattr(os, "sysconf"):
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            if page_size > 0 and phys_pages > 0:
                info["ram_gb"] = round((page_size * phys_pages) / (1024**3), 1)
    except Exception:
        pass
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            line = (out.stdout or "").strip().splitlines()
            if line:
                first = line[0].split(",")
                if len(first) >= 1:
                    info["gpu"] = first[0].strip()
                if len(first) >= 2:
                    raw = first[1].strip().lower().replace("mib", "").strip()
                    try:
                        info["vram_gb"] = round(int(raw) / 1024, 1)
                    except ValueError:
                        pass
        except Exception:
            pass
    info.setdefault("host_class", _guess_host_class())
    return info


def _guess_host_class() -> str:
    if os.environ.get("CI") in {"1", "true", "True"}:
        return "ci"
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "container"
    machine = platform.machine().lower()
    if machine.startswith("aarch64") or machine.startswith("arm"):
        return "pi"
    return "unknown"


# --- Concrete adapters -------------------------------------------------------


class MockRunner:
    name = "mock"
    version = mock_runner.RUNNER_VERSION
    adapter = "mock"

    def __init__(self, *, fail_task_ids: list[str] | None = None) -> None:
        self.fail_task_ids = fail_task_ids

    def prepare(self, model_spec: ModelSpec, workspace: Path) -> RunContext:
        workspace.mkdir(parents=True, exist_ok=True)
        return RunContext(
            workspace=workspace,
            started_at=_dt.datetime.now(_dt.timezone.utc),
        )

    def run_pack(
        self, pack: dict, model_spec: ModelSpec, ctx: RunContext, *, out_dir: Path
    ) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = mock_runner.run(
            pack,
            model_spec.to_dict(),
            out_dir,
            fail_task_ids=self.fail_task_ids,
        )
        # Stamp adapter + hardware in the artifact even though mock_runner
        # already filled most of it. Keeps the contract centralized here.
        artifact["runner"] = {
            "name": self.name,
            "version": self.version,
            "adapter": self.adapter,
        }
        artifact.setdefault("hardware", {"host_class": "ci"})
        return artifact

    def teardown(self, ctx: RunContext) -> None:
        # MockRunner owns nothing outside `workspace`. Caller manages it.
        return


class OpenclawRunner:
    """Runner that drives an isolated frankclaw gateway via subprocess.

    Isolation strategy (no global state mutation):
      * For every run, build a private `OPENCLAW_HOME` directory by cloning
        the user-provided baseline config + workspace into the per-run
        scratch path.
      * Set `OPENCLAW_CONFIG_PATH` and `OPENCLAW_HOME` env vars when invoking
        the orchestrator subprocess so frankclaw resolves all state into
        that scratch tree (frankclaw `resolveConfigDir` honors these).
      * Never edit `~/.openclaw/openclaw.json` and never delete the user's
        workspace memory files.

    The actual benchmark dispatch is delegated to an external command
    (`dispatch_cmd`). The contract is:
      - dispatch_cmd writes a v1 run artifact to `<out_dir>/run.json`
      - dispatch_cmd takes env overrides (OPENCLAW_HOME, OPENCLAW_CONFIG_PATH,
        JAKE_MODEL_SPEC_JSON, JAKE_PACK_PATH, JAKE_OUT_DIR) and CLI args.

    For local dev the `dispatch_cmd` defaults to a stub that just shells out
    to `harness/scripts/run-model-benchmark.sh` if it exists; otherwise the
    runner raises a clear error so callers know they need to supply a
    dispatch command (e.g. when running in a docker image without the
    legacy script set).
    """

    name = "openclaw"
    version = "0.1.0"
    adapter = "subprocess"

    def __init__(
        self,
        *,
        baseline_config: Path | None = None,
        baseline_home: Path | None = None,
        dispatch_cmd: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        timeout_seconds: int = 7200,
    ) -> None:
        self.baseline_config = (
            Path(baseline_config) if baseline_config else None
        )
        self.baseline_home = (
            Path(baseline_home) if baseline_home else None
        )
        self.dispatch_cmd = dispatch_cmd
        self.extra_env = dict(extra_env or {})
        self.timeout_seconds = timeout_seconds

    def prepare(self, model_spec: ModelSpec, workspace: Path) -> RunContext:
        workspace.mkdir(parents=True, exist_ok=True)
        scratch_home = workspace / "openclaw-home"
        scratch_home.mkdir(exist_ok=True)
        if self.baseline_home and self.baseline_home.exists():
            _copy_tree(self.baseline_home, scratch_home)
        scratch_config = scratch_home / "openclaw.json"
        if self.baseline_config and self.baseline_config.exists():
            scratch_config.write_text(
                self.baseline_config.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        elif not scratch_config.exists():
            # Synthesize a minimal config so the gateway has something to read.
            scratch_config.write_text(json.dumps({"agents": {}}, indent=2) + "\n",
                                      encoding="utf-8")
        return RunContext(
            workspace=workspace,
            started_at=_dt.datetime.now(_dt.timezone.utc),
            extras={
                "scratch_home": str(scratch_home),
                "scratch_config": str(scratch_config),
            },
        )

    def run_pack(
        self, pack: dict, model_spec: ModelSpec, ctx: RunContext, *, out_dir: Path
    ) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        spec_path = ctx.workspace / "model-spec.json"
        spec_path.write_text(
            json.dumps(model_spec.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pack_path = ctx.workspace / "task-pack.json"
        pack_path.write_text(
            json.dumps(pack, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(self.extra_env)
        env["OPENCLAW_HOME"] = ctx.extras["scratch_home"]
        env["OPENCLAW_CONFIG_PATH"] = ctx.extras["scratch_config"]
        env["JAKE_MODEL_SPEC_JSON"] = str(spec_path)
        env["JAKE_PACK_PATH"] = str(pack_path)
        env["JAKE_OUT_DIR"] = str(out_dir)

        cmd = self._resolve_dispatch_cmd()
        try:
            subprocess.run(
                cmd, env=env, check=True, timeout=self.timeout_seconds,
            )
        except subprocess.CalledProcessError as e:
            raise RunnerError(
                f"OpenclawRunner dispatch failed exit={e.returncode}: {' '.join(cmd)}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RunnerError(
                f"OpenclawRunner dispatch timed out after {self.timeout_seconds}s: "
                f"{' '.join(cmd)}"
            ) from e

        artifact_path = out_dir / "run.json"
        if not artifact_path.exists():
            raise RunnerError(
                f"dispatch_cmd did not produce {artifact_path}. "
                "Adapter contract: write run.json to JAKE_OUT_DIR."
            )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["runner"] = {
            "name": self.name,
            "version": self.version,
            "adapter": self.adapter,
        }
        artifact.setdefault("hardware", detect_hardware())
        return artifact

    def teardown(self, ctx: RunContext) -> None:
        # Caller decides whether to keep the scratch dir for debugging.
        return

    def _resolve_dispatch_cmd(self) -> list[str]:
        if self.dispatch_cmd:
            return list(self.dispatch_cmd)
        legacy = Path(__file__).resolve().parent.parent / "scripts" / "run-model-benchmark.sh"
        if legacy.exists():
            return ["bash", str(legacy)]
        raise RunnerError(
            "OpenclawRunner has no dispatch_cmd and no legacy run-model-benchmark.sh "
            "is available. Pass dispatch_cmd=[...] when constructing the runner."
        )


# --- Errors ------------------------------------------------------------------


class RunnerError(RuntimeError):
    """Raised when a runner adapter cannot complete its contract."""


# --- Registry ----------------------------------------------------------------


def build_runner(
    kind: str,
    *,
    baseline_config: Path | None = None,
    baseline_home: Path | None = None,
    dispatch_cmd: list[str] | None = None,
    fail_task_ids: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 7200,
) -> Runner:
    """Construct a runner by name. Keeps callers free of import paths."""

    if kind == "mock":
        return MockRunner(fail_task_ids=fail_task_ids)
    if kind == "openclaw":
        return OpenclawRunner(
            baseline_config=baseline_config,
            baseline_home=baseline_home,
            dispatch_cmd=dispatch_cmd,
            extra_env=extra_env,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"unknown runner kind: {kind!r} (expected 'mock' or 'openclaw')")


# --- Helpers -----------------------------------------------------------------


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy `src` into `dst` (existing). Skips obvious large/cache dirs."""

    SKIP_DIRS = {"node_modules", "__pycache__", ".git", "logs", "sessions"}
    for entry in src.iterdir():
        if entry.name in SKIP_DIRS:
            continue
        target = dst / entry.name
        if entry.is_dir():
            target.mkdir(exist_ok=True)
            _copy_tree(entry, target)
        else:
            try:
                target.write_bytes(entry.read_bytes())
            except OSError:
                # Best-effort copy; permissions or symlinks may fail.
                continue
