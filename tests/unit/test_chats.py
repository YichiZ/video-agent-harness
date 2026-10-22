import time

import pytest

from server import chats
from server.agent import titles
from server.workspace import Workspace

USER = {"role": "user", "text": "", "tools": [], "interrupted": False}
REPLY = {"role": "assistant", "text": "an answer", "tools": [], "interrupted": False}


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path / "abc")


def ask(chat, question):
    chat.save_turn({**USER, "text": question}, REPLY)


def test_a_new_chat_has_no_folder_until_its_first_turn(ws):
    chat = chats.new(ws)
    assert not chat.exists and chat.turns() == [] and chats.chats(ws) == []
    ask(chat, "What happens   at the start?")
    assert chat.exists and [c.id for c in chats.chats(ws)] == [chat.id]
    assert chat.summary()["title"] == "What happens at the start?"
    assert len(chat.turns()) == 2


def test_chats_list_most_recently_updated_first(ws):
    first, second = chats.new(ws), chats.new(ws)
    ask(first, "one")
    ask(second, "two")
    assert [c.id for c in chats.chats(ws)] == [second.id, first.id]
    time.sleep(0.01)
    ask(first, "one more")
    assert chats.latest(ws).id == first.id
    assert first.summary()["title"] == "one"  # the first question names it


def test_ids_sort_by_creation_and_are_validated(ws):
    a = chats.new_id()
    time.sleep(0.002)
    assert chats.valid_id(a) and a < chats.new_id()
    for bad in ["../x", "new", "", "A" * 18, a + "0", a + "\n"]:
        with pytest.raises(ValueError):
            chats.get(ws, bad)


def test_rename_survives_a_late_generated_title(ws):
    chat = chats.new(ws)
    assert not chat.set_title("too early", by_user=False)  # no folder yet
    ask(chat, "q")
    assert chat.set_title("Mine", by_user=True)
    assert not chat.set_title("Generated", by_user=False)
    assert chat.summary()["title"] == "Mine"
    ask(chat, "q2")
    assert chat.summary()["title"] == "Mine"
    assert chat.set_title("Mine again", by_user=True)


def test_generated_title_replaces_the_fallback(ws):
    chat = chats.new(ws)
    ask(chat, "q")
    assert chat.set_title("Spikes in the opening", by_user=False)
    assert chat.summary()["title"] == "Spikes in the opening"


def test_old_chat_and_session_move_into_a_chat(ws):
    ws.dir.mkdir(parents=True)
    ws.write_json("chat.json", {"turns": [{**USER, "text": "old question"}, REPLY]})
    ws.write_json("session.json", {"session_id": "old-session"})
    (chat,) = chats.chats(ws)
    assert not ws.path("chat.json").exists() and not ws.path("session.json").exists()
    assert chat.session_id() == "old-session" and chat.turns()[0]["text"] == "old question"
    assert chat.summary()["title"] == "old question" and chat.summary()["updated"] > 0
    assert [c.id for c in chats.chats(ws)] == [chat.id]  # migrated once


def test_short_title_cuts_at_a_word():
    long = "word " * 30
    title = chats.short_title(long)
    assert len(title) <= chats.TITLE_CHARS and title.endswith("…") and "  " not in title


def test_clean_title():
    assert titles.clean(' "Speed of the spikes." ') == "Speed of the spikes"
    assert titles.clean("one two three four five six seven") == "one two three four five six"
    with pytest.raises(ValueError):
        titles.clean("  ")


def test_a_chat_without_turns_is_not_listed(ws):
    chat = chats.new(ws)
    chat.remember_session("stopped-at-once")  # Stop before any output: no turn saved
    assert chat.exists and chats.chats(ws) == []
