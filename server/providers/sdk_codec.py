"""JSON round-trip for `claude_agent_sdk` messages.

Recorded agent streams (`tests/fixtures/agent/*.json`) store each message as
its dataclass fields plus a `_type` tag, for example
`{"_type": "TextBlock", "text": "Hi"}`. Decoding rebuilds the real SDK
dataclasses, so code under test sees exactly the types a live run produces.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from claude_agent_sdk import types as sdk_types

TYPE_KEY = "_type"


def _sdk_class(name: str) -> type:
    cls = getattr(sdk_types, name, None)
    if cls is None or not dataclasses.is_dataclass(cls):
        raise ValueError(f"unknown claude_agent_sdk type {name!r} in recorded stream")
    return cls


def encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {TYPE_KEY: type(value).__name__}
        for f in dataclasses.fields(value):
            out[f.name] = encode(getattr(value, f.name))
        return out
    if isinstance(value, list | tuple):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    return value


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if TYPE_KEY in value:
            cls = _sdk_class(value[TYPE_KEY])
            known = {f.name for f in dataclasses.fields(cls)}
            # Ignore fields a newer SDK recorded that this SDK version lacks.
            kwargs = {k: decode(v) for k, v in value.items() if k in known}
            return cls(**kwargs)
        return {k: decode(v) for k, v in value.items()}
    return value
