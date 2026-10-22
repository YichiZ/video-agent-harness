import threading

import pytest

from server.config import REPO_ROOT, Settings, load_release
from server.ingest import STATUS, Cancelled, Rejected, Step, StepContext, run_steps, steps
from server.workspace import Workspace


def test_registry_has_probe():
    assert "probe" in steps()


def test_requires_are_included_and_skip_when_done(tmp_path, monkeypatch):
    calls = []

    def make(name, requires=()):
        def run(ctx):
            calls.append(name)
            ctx.workspace.write_json(f"{name}.json", {})

        return Step(name=name, outputs=[f"{name}.json"], run=run, requires=list(requires))

    fake = {"a": make("a"), "b": make("b", ["a"]), "c": make("c", ["b"]), "d": make("d")}
    monkeypatch.setattr("server.ingest.steps", lambda: fake)
    ws = Workspace(tmp_path / "ws")
    ws.dir.mkdir()
    ctx = StepContext(ws, Settings.load(), load_release(REPO_ROOT / "release.json"))

    results = run_steps(ctx, ["c"])
    assert sorted(r.name for r in results) == ["a", "b", "c"]
    assert calls == ["a", "b", "c"]

    results = run_steps(ctx, ["c"], force=["c"])
    assert sorted((r.name, r.state) for r in results) == [
        ("a", "skipped"),
        ("b", "skipped"),
        ("c", "done"),
    ]


def test_step_with_a_newer_input_is_not_done(tmp_path):
    ws = Workspace(tmp_path)
    step = Step(name="i", outputs=["out.json"], run=lambda ctx: None, inputs=["in.json"])
    ws.write_json("out.json", {})
    assert step.is_done(ws)  # a missing input never makes it stale
    ws.write_json("in.json", {})
    assert not step.is_done(ws)
    ws.write_json("out.json", {})
    assert step.is_done(ws)


def _ctx(tmp_path, env=None, monkeypatch=None):
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    ws = Workspace(tmp_path / "ws")
    ws.dir.mkdir()
    return StepContext(ws, Settings.load(), load_release(REPO_ROOT / "release.json"))


def _step(name, requires=(), run=None):
    def default(ctx):
        ctx.workspace.write_json(f"{name}.json", {})

    return Step(name=name, outputs=[f"{name}.json"], run=run or default, requires=list(requires))


def test_independent_steps_run_in_parallel(tmp_path, monkeypatch):
    barrier = threading.Barrier(2, timeout=5)  # deadlocks unless b and c overlap

    def meet(name):
        def run(ctx):
            barrier.wait()
            ctx.workspace.write_json(f"{name}.json", {})

        return run

    fake = {"a": _step("a"), "b": _step("b", ["a"], meet("b")), "c": _step("c", ["a"], meet("c"))}
    monkeypatch.setattr("server.ingest.steps", lambda: fake)
    results = run_steps(_ctx(tmp_path))
    assert {r.name: r.state for r in results} == {"a": "done", "b": "done", "c": "done"}


def test_a_failure_blocks_dependents_and_others_finish(tmp_path, monkeypatch):
    fake = {
        "a": _step("a"),
        "b": _step("b", ["a"]),
        "c": _step("c", ["a"]),
        "d": _step("d", ["b", "c"]),
    }
    monkeypatch.setattr("server.ingest.steps", lambda: fake)
    env = {"HARNESS_FAKE_PROVIDERS": "1", "HARNESS_FAKE_FAIL": "b"}
    ctx = _ctx(tmp_path, env, monkeypatch)
    results = {r.name: r for r in run_steps(ctx)}
    assert {n: r.state for n, r in results.items()} == {
        "a": "done",
        "b": "failed",
        "c": "done",
        "d": "blocked",
    }
    assert "injected failure" in results["b"].summary
    status = ctx.workspace.read_json(STATUS)
    assert {n: s["state"] for n, s in status["steps"].items()} == {
        "a": "done",
        "b": "failed",
        "c": "done",
        "d": "blocked",
    }

    monkeypatch.delenv("HARNESS_FAKE_FAIL")
    retry = StepContext(ctx.workspace, Settings.load(), ctx.release)
    states = {r.name: r.state for r in run_steps(retry, force=["b"])}
    assert states == {"a": "skipped", "b": "done", "c": "skipped", "d": "done"}


def test_chat_ready_once_transcribe_and_overview_are_done(tmp_path, monkeypatch):
    fake = {n: _step(n) for n in ("transcribe", "overview", "captions")}
    monkeypatch.setattr("server.ingest.steps", lambda: fake)
    ctx = _ctx(tmp_path)
    run_steps(ctx, ["transcribe"])
    assert ctx.workspace.read_json(STATUS)["chat_ready"] is False
    run_steps(ctx, ["overview"])
    status = ctx.workspace.read_json(STATUS)
    assert status["chat_ready"] is True and status["steps"]["captions"]["state"] == "pending"


def test_rejected_is_raised_after_the_run(tmp_path, monkeypatch):
    def reject(ctx):
        raise Rejected("too long")

    fake = {"probe": _step("probe", run=reject), "b": _step("b", ["probe"])}
    monkeypatch.setattr("server.ingest.steps", lambda: fake)
    ctx = _ctx(tmp_path)
    with pytest.raises(Rejected, match="too long"):
        run_steps(ctx)
    assert ctx.workspace.read_json(STATUS)["steps"]["b"]["state"] == "blocked"


def test_cancel_stops_steps_that_have_not_started(tmp_path, monkeypatch):
    def cancel_then_write(ctx):
        ctx.cancel.set()
        ctx.workspace.write_json("a.json", {})

    fake = {"a": _step("a", run=cancel_then_write), "b": _step("b", ["a"]), "c": _step("c", ["b"])}
    monkeypatch.setattr("server.ingest.steps", lambda: fake)
    ctx = _ctx(tmp_path)
    states = {r.name: r.state for r in run_steps(ctx)}
    assert states == {"a": "done", "b": "cancelled", "c": "cancelled"}
    saved = ctx.workspace.read_json(STATUS)["steps"]
    assert {n: s["state"] for n, s in saved.items()} == states
    assert not ctx.workspace.path("b.json").exists()


def test_a_step_that_stops_on_cancel_is_cancelled(tmp_path, monkeypatch):
    def stop(ctx):
        raise Cancelled

    monkeypatch.setattr("server.ingest.steps", lambda: {"a": _step("a", run=stop)})
    assert [(r.name, r.state) for r in run_steps(_ctx(tmp_path))] == [("a", "cancelled")]
