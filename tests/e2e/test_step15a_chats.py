"""Step 15a e2e: several chats per video, over the server and in the browser.

On `cuts.mp4` (everywhere; the agent replays `cuts-chat.json` / `cuts-resume.json`):
- Two chats on one video each keep their own history and resume their own session;
  each gets a `chat_title` frame after its first answer.
- A second tab can't start a turn in a chat that is already running one.
- Rename, delete (refused while a turn runs), and a re-upload that keeps the chats.
- In the browser: a second chat, switching shows the right history, rename,
  delete, a reload lands on the most recent chat, and a re-upload keeps them.
Live, one real title from the title model.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from playwright.sync_api import expect
from websockets.sync.client import connect

from server.agent.titles import FAKE_TITLE, write_title
from server.config import Settings, load_release
from tests.e2e.test_step10_server import server, upload, wait_for, ws_url
from tests.e2e.test_step11a_ui import drop, wait_all_done
from tests.e2e.test_step11b_chat_ui import ask

TIMEOUT_MS = 60_000  # offline, cuts.mp4 ingests and answers in seconds
SLOW = {"HARNESS_FAKE_STREAM_DELAY": "0.1"}  # a turn lasts a few seconds


def history(ws) -> dict:
    frame = json.loads(ws.recv(timeout=10))
    assert frame["type"] == "history", frame
    return frame


def until(ws, kind: str) -> list[dict]:
    frames = []
    while True:
        frame = json.loads(ws.recv(timeout=60))
        frames.append(frame)
        if frame["type"] == kind:
            return frames
        assert frame["type"] != "error", frame


def turn(ws, text: str) -> str:
    ws.send(json.dumps({"type": "user_message", "text": text}))
    return "".join(f["text"] for f in until(ws, "result") if f["type"] == "text_delta")


def chats(base: str) -> list[dict]:
    return httpx.get(f"{base}/video/current/chats").json()


def check_two_chats(base: str) -> tuple[str, str]:
    with connect(ws_url(base)) as ws:
        first = history(ws)
        assert first["turns"] == [] and first["chat_id"]
        assert "[00:02]" in turn(ws, "Where is the first cut?")
        titled = until(ws, "chat_title")[-1]
        assert titled == {"type": "chat_title", "chat_id": first["chat_id"], "title": FAKE_TITLE}
    with connect(ws_url(base) + "?chat=new") as ws:
        second = history(ws)
        assert second["turns"] == [] and second["chat_id"] != first["chat_id"]
        turn(ws, "Second chat question")
        until(ws, "chat_title")
    a, b = first["chat_id"], second["chat_id"]
    assert [c["id"] for c in chats(base)] == [b, a]
    with connect(ws_url(base) + f"?chat={a}") as ws:  # resumes chat a's own session
        opened = history(ws)
        assert [t["text"] for t in opened["turns"] if t["role"] == "user"] == [
            "Where is the first cut?"
        ]
        assert "[00:04]" in turn(ws, "Any more cuts?")
    with connect(ws_url(base)) as ws:  # no chat named: the most recently updated
        assert history(ws)["chat_id"] == a
    return a, b


def check_one_turn_per_chat(base: str, chat: str) -> None:
    with (
        connect(ws_url(base) + f"?chat={chat}") as one,
        connect(ws_url(base) + f"?chat={chat}") as two,
    ):
        history(one), history(two)
        one.send(json.dumps({"type": "user_message", "text": "Any more cuts?"}))
        until(one, "text_delta")
        two.send(json.dumps({"type": "user_message", "text": "me too"}))
        refused = _error(two)
        assert refused["refused"] and "already running" in refused["message"]
        deleted = httpx.delete(f"{base}/video/current/chats/{chat}")
        assert deleted.status_code == 409
        one.send(json.dumps({"type": "interrupt"}))
        assert until(one, "result")[-1]["interrupted"]


def _error(ws) -> dict:
    while True:
        frame = json.loads(ws.recv(timeout=10))
        if frame["type"] == "error":
            return frame


def check_rename_delete_reupload(base: str, video, a: str, b: str) -> None:
    renamed = httpx.patch(f"{base}/video/current/chats/{b}", json={"title": "  My   chat "})
    assert renamed.json()["title"] == "My chat"
    assert httpx.patch(f"{base}/video/current/chats/{b}", json={"title": ""}).status_code == 422
    assert (
        httpx.patch(f"{base}/video/current/chats/not-an-id", json={"title": "x"}).status_code == 404
    )
    assert httpx.delete(f"{base}/video/current/chats/{a}").status_code == 204
    assert httpx.delete(f"{base}/video/current/chats/{a}").status_code == 404
    assert upload(base, video).status_code == 200
    wait_for(base, lambda s: s["chat_ready"])
    assert [(c["id"], c["title"]) for c in chats(base)] == [(b, "My chat")]


def test_chats_over_the_server(cli, test_media):
    video = test_media / "cuts.mp4"
    with server(cli, SLOW) as base:
        assert upload(base, video).status_code == 200
        wait_for(base, lambda s: s["chat_ready"])
        a, b = check_two_chats(base)
        check_one_turn_per_chat(base, a)
        check_rename_delete_reupload(base, video, a, b)


def user_texts(page) -> list[str]:
    return page.locator('[data-role="user"]').all_inner_texts()


def test_chats_in_the_browser(cli, test_media, page):
    video = test_media / "cuts.mp4"
    nav = page.get_by_role("navigation", name="chats")
    with server(cli) as base:
        page.goto(base)
        drop(page, video)
        wait_all_done(page)

        ask(page, "First chat question")
        expect(nav.get_by_text(FAKE_TITLE)).to_be_visible(timeout=TIMEOUT_MS)
        nav.get_by_role("button", name="New chat").click()
        expect(page.locator('[data-role="user"]')).to_have_count(0)
        ask(page, "Second chat question")
        expect(nav.locator("li")).to_have_count(2, timeout=TIMEOUT_MS)
        expect(nav.locator("li[aria-current]")).to_have_count(1)

        nav.locator("li").nth(1).locator("button").first.click()  # the older chat
        expect(page.locator('[data-role="user"]')).to_have_text(["First chat question"])
        nav.locator("li").nth(0).locator("button").first.click()
        expect(page.locator('[data-role="user"]')).to_have_text(["Second chat question"])

        page.evaluate("window.prompt = () => 'Renamed chat'")  # the dialog fixture can't type
        nav.locator("li[aria-current]").get_by_role("button", name=f"Rename {FAKE_TITLE}").click()
        expect(nav.get_by_text("Renamed chat")).to_be_visible()

        older = nav.locator("li").nth(1)
        older.get_by_role("button", name=f"Delete {FAKE_TITLE}").click()  # confirm accepted
        expect(nav.locator("li")).to_have_count(1)

        nav.get_by_role("button", name="New chat").click()
        expect(nav.locator("li[aria-current]")).to_have_count(0)  # the new chat is open
        ask(page, "Third chat question")
        expect(nav.locator("li")).to_have_count(2, timeout=TIMEOUT_MS)
        page.reload()  # lands on the most recent chat
        expect(page.locator('[data-role="user"]')).to_have_text(["Third chat question"])

        drop(page, video)  # the same video again keeps its chats
        expect(page.get_by_label("message")).to_be_enabled(timeout=TIMEOUT_MS)
        expect(page.locator('[data-role="user"]')).to_have_text(["Third chat question"])
        expect(nav.locator("li")).to_have_count(2)
        expect(nav.get_by_text("Renamed chat")).to_be_visible()


@pytest.mark.live
def test_live_title():
    """One real title from `release.title_model`, in whichever Claude auth mode is set up."""
    answer = "The virus attaches to a host cell with the spikes on its surface [00:04]."
    title = asyncio.run(
        write_title("What does the virus attach to a host cell with?", answer, Settings.load(),
                    load_release())
    )  # fmt: skip
    assert title != FAKE_TITLE and 1 <= len(title.split()) <= 6, title
