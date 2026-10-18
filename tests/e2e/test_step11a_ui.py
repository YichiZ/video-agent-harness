"""Step 11a e2e: Playwright drives the built UI against the real server.

- Drop a video on the drop zone and wait for every ingest step to show as done.
- Click the third transcript word: the player lands within 0.2 s of its start.
- Click a shot thumbnail: the player seeks to the shot's start.

`short` (Video-MME, skips in CI) covers the transcript; `cuts.mp4` has no speech
and covers dropping, the ingest panel and the shot strip everywhere.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from tests.e2e.test_step10_server import READY_TIMEOUT_S, server

SEEK_TOLERANCE_S = 0.2


def drop(page, video: Path) -> None:
    """Dispatch a real `drop` event carrying the file, as a drag from the desktop would."""
    data = base64.b64encode(video.read_bytes()).decode()
    page.evaluate(
        """([name, data]) => {
            const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
            const transfer = new DataTransfer();
            transfer.items.add(new File([bytes], name, { type: "video/mp4" }));
            const zone = document.querySelector('[data-testid="drop-zone"]');
            zone.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true }));
        }""",
        [video.name, data],
    )


def wait_all_done(page) -> None:
    page.wait_for_function(
        """() => {
            const steps = [...document.querySelectorAll("[data-step]")];
            return steps.length > 0 && steps.every((s) => s.dataset.state === "done");
        }""",
        timeout=READY_TIMEOUT_S * 1000,
    )


def player_time(page) -> float:
    return page.evaluate("document.querySelector('video').currentTime")


def check_seek(page, selector: str) -> None:
    target = page.locator(selector)
    start = float(target.get_attribute("data-start"))
    target.click()
    page.wait_for_function(
        "([t, tol]) => Math.abs(document.querySelector('video').currentTime - t) <= tol",
        arg=[start, SEEK_TOLERANCE_S],
        timeout=10_000,
    )
    assert abs(player_time(page) - start) <= SEEK_TOLERANCE_S


def open_and_drop(page, base: str, video: Path) -> None:
    page.goto(base)
    page.wait_for_selector('[data-testid="drop-zone"]')
    drop(page, video)
    wait_all_done(page)
    page.wait_for_selector("video")
    page.wait_for_function("document.querySelector('video').readyState >= 1")


def test_ui_cuts(cli, test_media, page):
    with server(cli) as base:
        open_and_drop(page, base, test_media / "cuts.mp4")
        assert page.locator("[data-shot]").count() == 5
        check_seek(page, '[data-shot="2"]')
        assert page.get_by_text("No speech in this video.").is_visible()


def test_ui_short(cli, videomme, page):
    with server(cli) as base:
        open_and_drop(page, base, videomme("short"))
        check_seek(page, '[data-word="2"]')
        check_seek(page, '[data-shot="1"]')


@pytest.mark.live
def test_ui_short_live(cli, videomme, page):
    test_ui_short(cli, videomme, page)
