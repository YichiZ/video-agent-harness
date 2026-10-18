from server.config import REPO_ROOT, Settings, load_release
from server.ingest import Step, StepContext, run_steps, steps
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
    assert [r.name for r in results] == ["a", "b", "c"]
    assert calls == ["a", "b", "c"]

    results = run_steps(ctx, ["c"], force=True)
    assert [(r.name, r.state) for r in results] == [
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
