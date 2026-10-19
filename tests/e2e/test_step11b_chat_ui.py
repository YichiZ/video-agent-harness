"""Step 11b e2e: Playwright drives the chat panel against the real server.

On `short` (Video-MME, skips in CI): the step 5 question streams in, its first
timestamp chip seeks the player to within 0.5 s, a tool row expands to show its
input, Stop ends the second answer, and after a reload the history is still
there; uploading the same video again clears the chat. Offline the agent replays
`TbaCxIJ_VP4-chat.json` (whose first two turns are these two questions); live it is
the real agent through the whole stack.
On `cuts.mp4` (everywhere) the input stays disabled until the video is chat-ready.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.e2e.test_step05_chat import GROUNDED, VISUAL
from tests.e2e.test_step10_server import server
from tests.e2e.test_step11a_ui import drop, player_time, wait_all_done

CHIP_TOLERANCE_S = 0.5
ANSWER_TIMEOUT_MS = 600_000


def ask(page, question: str) -> None:
    box = page.get_by_label("message")
    expect(box).to_be_enabled(timeout=ANSWER_TIMEOUT_MS)
    box.fill(question)
    box.press("Enter")


def check_chat(page, base: str, video) -> None:
    page.goto(base)
    drop(page, video)
    wait_all_done(page)

    ask(page, GROUNDED)
    answer = page.locator('[data-role="assistant"]').last
    expect(page.get_by_role("button", name="Stop")).to_be_visible()  # the turn is streaming
    expect(answer).not_to_have_text("", timeout=ANSWER_TIMEOUT_MS)  # partial text arrives
    expect(page.get_by_role("button", name="Send")).to_be_visible(timeout=ANSWER_TIMEOUT_MS)
    assert "spike" in answer.inner_text().lower()

    chip = answer.locator("[data-chip]").first
    start = float(chip.get_attribute("data-chip"))
    chip.click()
    page.wait_for_function(
        "([t, tol]) => Math.abs(document.querySelector('video').currentTime - t) <= tol",
        arg=[start, CHIP_TOLERANCE_S],
    )
    assert abs(player_time(page) - start) <= CHIP_TOLERANCE_S

    row = answer.locator("[data-tool]").first
    expect(row.locator("[data-tool-input]")).not_to_be_visible()
    row.locator("summary").click()
    expect(row.locator("[data-tool-input]")).to_be_visible()
    assert "{" in row.locator("[data-tool-input]").inner_text()

    ask(page, VISUAL)
    stop = page.get_by_role("button", name="Stop")
    second = page.locator('[data-role="assistant"]').last
    expect(second).not_to_have_text("", timeout=ANSWER_TIMEOUT_MS)
    stop.click()
    expect(page.get_by_text("(stopped)")).to_be_visible(timeout=ANSWER_TIMEOUT_MS)
    expect(page.get_by_role("button", name="Send")).to_be_visible()

    page.reload()
    expect(page.locator('[data-role="user"]')).to_have_count(2)
    expect(page.locator('[data-role="user"]').first).to_have_text(GROUNDED)
    expect(page.locator('[data-role="assistant"]').first).to_contain_text("pike")
    expect(page.get_by_text("(stopped)")).to_be_visible()

    drop(page, video)  # the same video again: a new upload starts a new chat
    expect(page.locator('[data-role="user"]')).to_have_count(0)
    page.reload()
    expect(page.get_by_label("message")).to_be_enabled(timeout=ANSWER_TIMEOUT_MS)
    expect(page.locator('[data-role="user"]')).to_have_count(0)


def test_chat_ui_short(cli, videomme, page):
    with server(cli, {"HARNESS_FAKE_STREAM_DELAY": "0.1"}) as base:  # time to see and press Stop
        check_chat(page, base, videomme("short"))


@pytest.mark.live
def test_chat_ui_short_live(cli, videomme, page):
    with server(cli) as base:
        check_chat(page, base, videomme("short"))


def test_input_waits_for_chat_ready(cli, test_media, page):
    with server(cli, {"HARNESS_FAKE_SLOW": "overview"}) as base:
        page.goto(base)
        drop(page, test_media / "cuts.mp4")
        expect(page.locator('[data-step="overview"]')).to_have_attribute("data-state", "running")
        expect(page.get_by_label("message")).to_be_disabled()
        expect(page.get_by_label("message")).to_be_enabled(timeout=30_000)
