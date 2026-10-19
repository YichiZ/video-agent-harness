from server.config import REPO_ROOT, Settings, load_release
from server.tracing import Tracer, Turn, release_hash


class Span:
    def __init__(self, log, name):
        self.log, self.name, self.trace_id = log, name, "t1"

    def start_observation(self, *, name, as_type, input):
        self.log.append(("start", name, as_type, input))
        return Span(self.log, name)

    def update(self, **kwargs):
        self.log.append(("update", self.name, kwargs))
        return self

    def end(self):
        self.log.append(("end", self.name))


def test_turn_records_tools_and_closes_open_spans():
    log = []
    turn = Turn(Span(log, "chat-turn"))
    assert turn.trace_id == "t1"
    turn.tool("a", "search", {"query": "spikes"})
    turn.tool_result("a", '{"hits": []}', False)
    turn.tool("b", "inspect", {})  # never answered (interrupted)
    turn.end("Spikes [00:05].", {"cost_usd": 0.1})
    assert ("start", "search", "tool", {"query": "spikes"}) in log
    assert ("end", "search") in log and ("end", "inspect") in log
    assert log[-1] == ("end", "chat-turn")
    assert log[-2][2]["output"] == "Spikes [00:05]." and log[-2][2]["level"] is None


def test_turn_never_raises():
    class Broken(Span):
        def update(self, **kwargs):
            raise RuntimeError("network down")

    Turn(Broken([], "chat-turn")).end("x", {})  # logged, not raised


def test_tracing_is_off_without_keys_or_with_fakes(monkeypatch):
    monkeypatch.setenv("HARNESS_ENV_FILE", "/nonexistent")
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    release = load_release(REPO_ROOT / "release.json")
    assert not Tracer(Settings.load(), release).enabled
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("HARNESS_FAKE_PROVIDERS", "1")
    tracer = Tracer(Settings.load(), release)
    assert not tracer.enabled and tracer.turn("hi", video_id="v", session_id=None).trace_id is None
    assert len(release_hash()) == 12
