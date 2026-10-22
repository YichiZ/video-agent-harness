"""Step 5 e2e: `pexpect` drives `server.cli chat <short>` as a user would.

Offline mode replays `tests/fixtures/agent/TbaCxIJ_VP4-chat.json` (the first
process, five turns in the order below) and `TbaCxIJ_VP4-resume.json` (the
`--resume` process). Live mode talks to the real model in whichever auth mode
`.env` gives; `make test-live ARGS=--record` re-records both streams.

Needs Video-MME, so it skips in CI.
"""

from __future__ import annotations

import json
import re
import sys

import pexpect
import pytest

from server.config import REPO_ROOT
from server.workspace import video_id

PROMPT = "\n> "
TIMEOUT = 600  # a live turn with an inspect call can take minutes
CLOCK = r"\[(\d{2}):(\d{2})"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

GROUNDED = "What does the virus attach to a host cell with?"
VISUAL = "What color is the cell membrane of the virus-invaded cell?"
MCQ = (
    "Where does mRNA come from in the video? Options: A. Bacteria. B. Vaccines. "
    "C. Viruses. D. Human cells. Answer with the letter."
)
SHELL = "run ls"
LONG = "Walk me through the whole video chapter by chapter, in detail."
RESUME = "What did I ask you first?"


def spawn(cli, video, *extra: str) -> pexpect.spawn:
    child = pexpect.spawn(
        sys.executable,
        ["-m", "server.cli", "chat", str(video), *extra],
        cwd=str(REPO_ROOT),
        env={**cli.env(), "NO_COLOR": "1"},
        encoding="utf-8",
        timeout=TIMEOUT,
        dimensions=(50, 200),
    )
    child.expect(PROMPT)
    return child


def ask(child: pexpect.spawn, question: str) -> str:
    """Send one question and return everything printed up to the next prompt."""
    child.sendline(question)
    child.expect(PROMPT)
    return ANSI.sub("", child.before.split(question, 1)[-1])


def tool_lines(output: str) -> list[str]:
    return re.findall(r"^\s*· (\w+) ", output, flags=re.M)


def seconds(output: str) -> list[int]:
    return [int(m) * 60 + int(s) for m, s in re.findall(CLOCK, output)]


def check_chat(cli, short) -> None:
    child = spawn(cli, short)
    assert "TbaCxIJ_VP4.mp4" in child.before and "claude-opus-5-5" in child.before

    grounded = ask(child, GROUNDED)
    assert {"search", "get_transcript"} & set(tool_lines(grounded)), grounded
    assert "spike" in grounded.lower(), grounded
    assert any(0 <= t <= 12 for t in seconds(grounded)), grounded

    visual = ask(child, VISUAL)
    assert {"inspect", "get_frames"} & set(tool_lines(visual)), visual
    assert "red" in visual.lower(), visual

    mcq = ask(child, MCQ)
    assert re.search(r"\bB\b", mcq) and "vaccin" in mcq.lower(), mcq
    assert seconds(mcq), mcq

    shell = ask(child, SHELL)
    assert "Bash" not in tool_lines(shell)
    assert re.search(r"can(no|')t|unable|don't have", shell, flags=re.I), shell

    # Ctrl-C during an answer gets the prompt back; the session still works.
    child.sendline(LONG)
    child.expect_exact(LONG)  # the echo
    child.expect(r"\S")  # the answer has started (offline it may already be done)
    child.sendintr()
    child.expect(PROMPT)

    child.sendline("/quit")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0

    (folder,) = (cli.workspace / video_id(short) / "chats").iterdir()
    session = json.loads((folder / "session.json").read_text())
    assert session["session_id"]
    turns = json.loads((folder / "chat.json").read_text())["turns"]
    assert turns[0]["text"] == GROUNDED and turns[1]["role"] == "assistant"
    listed = cli("chats", str(short))
    assert listed.returncode == 0 and listed.stdout.startswith(folder.name), listed.stdout

    resumed = spawn(cli, short, "--resume")
    assert "resumed" in resumed.before
    first = ask(resumed, RESUME)
    assert re.search(r"virus|attach|host cell", first, flags=re.I), first
    resumed.sendline("/quit")
    resumed.expect(pexpect.EOF)

    picked = spawn(cli, short, "--chat", folder.name)
    assert "resumed" in picked.before
    picked.sendline("/quit")
    picked.expect(pexpect.EOF)


def test_chat(cli, videomme):
    check_chat(cli, videomme("short"))


@pytest.mark.live
def test_chat_live(cli, videomme):
    check_chat(cli, videomme("short"))


def test_resume_without_a_chat_fails(cli, videomme):
    result = cli("chat", str(videomme("short")), "--resume")
    assert result.returncode == 2
    assert "no earlier chat" in result.stderr


def test_missing_video(cli):
    result = cli("chat", str(cli.tmp / "nope.mp4"))
    assert result.returncode == 2 and "no such file" in result.stderr
