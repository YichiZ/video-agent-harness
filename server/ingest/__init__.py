"""Ingest steps and a simple sequential runner.

Each step is a module listed in `STEP_MODULES` that defines `STEP = Step(...)`.
To add a step (for example `transcribe` in plan step 2), write
`server/ingest/<name>.py` with a `STEP` and append the module to `STEP_MODULES`.

A step is skipped when all of its `outputs` already exist in the workspace, so
re-runs are cheap; `force=True` re-runs it. Steps run in list order. Plan step 9
replaces `run_steps` with parallel orchestration and `status.json`.

A step signals bad input (a video the harness will not accept) by raising
`Rejected`, which the CLI turns into exit code 2. Any other exception is a
step failure (exit code 3).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cache

from server.config import Release, Settings
from server.workspace import Workspace

# In run order.
STEP_MODULES = [
    "server.ingest.probe",
    "server.ingest.transcribe",
    "server.ingest.overview",
    "server.ingest.shots",
]


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

    def is_done(self, workspace: Workspace) -> bool:
        return all(workspace.path(name).exists() for name in self.outputs)


@dataclass(frozen=True)
class StepResult:
    name: str
    state: str  # "done" or "skipped"
    summary: str | None = None


@cache
def steps() -> dict[str, Step]:
    loaded = [importlib.import_module(m).STEP for m in STEP_MODULES]
    return {s.name: s for s in loaded}


def run_steps(
    ctx: StepContext,
    names: Iterable[str] | None = None,
    *,
    force: bool = False,
    on_result: Callable[[StepResult], None] | None = None,
) -> list[StepResult]:
    """Run the named steps (all by default) in registry order.

    Steps listed in `requires` (transitively) are included too, and are skipped
    if already done, so `--only transcribe` on a fresh video still probes it.
    """
    registry = steps()
    wanted = set(names) if names is not None else set(registry)
    unknown = wanted - set(registry)
    if unknown:
        raise KeyError(f"unknown ingest step(s): {', '.join(sorted(unknown))}")

    needed = set(wanted)
    frontier = list(wanted)
    while frontier:
        for dep in registry[frontier.pop()].requires:
            if dep not in needed:
                needed.add(dep)
                frontier.append(dep)

    results = []
    for step in registry.values():
        if step.name not in needed:
            continue
        named = step.name in wanted
        if step.is_done(ctx.workspace) and not (force and named):
            result = StepResult(step.name, "skipped")
        else:
            result = StepResult(step.name, "done", step.run(ctx))
        results.append(result)
        if on_result:
            on_result(result)
    return results
