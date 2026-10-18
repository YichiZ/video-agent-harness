"""Ingest steps and the runner that orchestrates them.

Each step is a module listed in `STEP_MODULES` that defines `STEP = Step(...)`.
To add a step, write `server/ingest/<name>.py` with a `STEP` and append the
module to `STEP_MODULES`.

`run_steps` runs steps in parallel threads, each as soon as the steps it
`requires` have finished. A step is skipped when its outputs already exist (and
are newer than its `inputs`); naming it in `force` re-runs it. If a step fails,
the others still finish; steps that need it are "blocked". Every state change
is written to `status.json`, which also says whether the video is chat-ready
(the `CHAT_READY` steps are done).

A step signals bad input (a video the harness will not accept) by raising
`Rejected`, which `run_steps` re-raises once the other steps have stopped and
the CLI turns into exit code 2. Any other exception is a step failure (exit 3).

With fake providers, `HARNESS_FAKE_FAIL=<step>[,<step>]` makes those steps fail
and `HARNESS_FAKE_SLOW=<step>[,<step>]` delays them, for tests.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from collections.abc import Callable, Collection, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache

from server.config import Release, Settings
from server.workspace import Workspace

# In run order.
STEP_MODULES = [
    "server.ingest.probe",
    "server.ingest.transcribe",
    "server.ingest.overview",
    "server.ingest.shots",
    "server.ingest.captions",
    "server.ingest.index",
    "server.ingest.proxy",
]

STATUS = "status.json"
CHAT_READY = ("transcribe", "overview")
"""Steps the agent needs before a chat can start; the rest can finish in the background."""
FAKE_SLOW_SECONDS = 10

log = logging.getLogger(__name__)


class Rejected(Exception):
    """The input video is not acceptable. The message is shown to the user as is."""


@dataclass(frozen=True)
class StepContext:
    workspace: Workspace
    settings: Settings
    release: Release


@dataclass(frozen=True)
class Step:
    name: str
    outputs: list[str]
    run: Callable[[StepContext], str | None]
    """Does the work and writes `outputs`. May return a one-line summary for the CLI."""
    requires: list[str] = field(default_factory=list)
    """Names of steps whose outputs this step reads."""

    inputs: list[str] = field(default_factory=list)
    """Files this step reads; if one is newer than an output, the step is not done."""

    def is_done(self, workspace: Workspace) -> bool:
        outputs = [workspace.path(name) for name in self.outputs]
        if not all(p.exists() for p in outputs):
            return False
        built = min(p.stat().st_mtime for p in outputs)
        inputs = [workspace.path(name) for name in self.inputs]
        return all(not p.exists() or p.stat().st_mtime <= built for p in inputs)


@dataclass(frozen=True)
class StepResult:
    name: str
    state: str  # "done", "skipped", "failed" or "blocked"
    summary: str | None = None
    """For "done", the step's one-line summary; for "failed" or "blocked", why."""

    @property
    def ok(self) -> bool:
        return self.state in ("done", "skipped")


def failures(results: Iterable[StepResult]) -> list[StepResult]:
    return [r for r in results if not r.ok]


@cache
def steps() -> dict[str, Step]:
    loaded = [importlib.import_module(m).STEP for m in STEP_MODULES]
    return {s.name: s for s in loaded}


def _with_requires(registry: dict[str, Step], wanted: set[str]) -> set[str]:
    needed = set(wanted)
    frontier = list(wanted)
    while frontier:
        for dep in registry[frontier.pop()].requires:
            if dep not in needed:
                needed.add(dep)
                frontier.append(dep)
    return needed


class _Status:
    """`status.json`: every step's state, rewritten (atomically) on each change."""

    def __init__(self, workspace: Workspace, registry: dict[str, Step]):
        self._workspace = workspace
        self._lock = threading.Lock()
        self._steps = {
            name: {"state": "done" if step.is_done(workspace) else "pending"}
            for name, step in registry.items()
        }
        self._write()

    def set(self, name: str, state: str, detail: str | None = None) -> None:
        with self._lock:
            entry = {"state": state, "updated_at": datetime.now(UTC).isoformat()}
            if detail:
                entry["detail"] = detail
            self._steps = {**self._steps, name: entry}
            self._write()

    def _write(self) -> None:
        ready = all(self._steps[n]["state"] == "done" for n in CHAT_READY if n in self._steps)
        self._workspace.write_json(STATUS, {"chat_ready": ready, "steps": self._steps})


def _run_one(ctx: StepContext, step: Step) -> str | None:
    if ctx.settings.fake_providers:
        if step.name in ctx.settings.fake_fail:
            raise RuntimeError(f"injected failure (HARNESS_FAKE_FAIL={step.name})")
        if step.name in ctx.settings.fake_slow:
            time.sleep(FAKE_SLOW_SECONDS)
    return step.run(ctx)


def run_steps(
    ctx: StepContext,
    names: Iterable[str] | None = None,
    *,
    force: Collection[str] = (),
    on_result: Callable[[StepResult], None] | None = None,
) -> list[StepResult]:
    """Run the named steps (all by default) and what they require, in parallel.

    Steps listed in `requires` (transitively) are included too, and are skipped
    if already done, so `--only transcribe` on a fresh video still probes it.
    Returns one result per step, in the order they finished.
    """
    registry = steps()
    wanted = set(names) if names is not None else set(registry)
    unknown = (wanted | set(force)) - set(registry)
    if unknown:
        raise KeyError(f"unknown ingest step(s): {', '.join(sorted(unknown))}")
    needed = _with_requires(registry, wanted)

    status = _Status(ctx.workspace, registry)
    pending = [s for s in registry.values() if s.name in needed]
    finished: dict[str, StepResult] = {}
    running: dict[Future, Step] = {}
    rejected: Rejected | None = None

    def finish(result: StepResult) -> None:
        finished[result.name] = result
        status.set(result.name, "done" if result.ok else result.state, result.summary)
        if on_result:
            on_result(result)

    with ThreadPoolExecutor(max_workers=max(1, len(pending))) as pool:
        while pending or running:
            for step in [s for s in pending if all(d in finished for d in s.requires)]:
                pending.remove(step)
                broken = [d for d in step.requires if not finished[d].ok]
                if broken:
                    finish(StepResult(step.name, "blocked", f"needs {', '.join(broken)}"))
                elif step.is_done(ctx.workspace) and step.name not in force:
                    finish(StepResult(step.name, "skipped"))
                else:
                    status.set(step.name, "running")
                    running[pool.submit(_run_one, ctx, step)] = step
            if not running:
                continue  # skipped or blocked steps may have unblocked others
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                step = running.pop(future)
                try:
                    finish(StepResult(step.name, "done", future.result()))
                except Rejected as e:
                    rejected = e
                    finish(StepResult(step.name, "failed", str(e)))
                except Exception as e:  # the step failed; the others carry on
                    log.exception("ingest step %s failed", step.name)
                    finish(StepResult(step.name, "failed", f"{type(e).__name__}: {e}"))
    if rejected:
        raise rejected
    return list(finished.values())
