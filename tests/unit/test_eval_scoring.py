from evals.scoring import check_citations, citations, content_words, summarize

TRANSCRIPT = {"segments": [{"start": 3.0, "end": 9.0, "text": "The virus attaches its spikes."}]}
SHOTS = [{"start_s": 40.0, "end_s": 45.0, "caption": "A red cell membrane", "action": "",
          "on_screen_text": ["American Express"], "entities": []}]  # fmt: skip


def test_citations_parse_times_and_ranges():
    assert citations("[00:04] and [01:02–01:10], [1:00:05]; not [note]") == [
        (4, 4), (62, 70), (3605, 3605),
    ]  # fmt: skip


def test_content_words_drop_stopwords_and_short_words():
    assert content_words("The video shows a RED membrane, which is red.") == {"membrane"}


def test_valid_needs_a_grounded_citation_inside_the_video():
    ok = check_citations("Spikes attach it [00:05].", 84, TRANSCRIPT, SHOTS)
    assert ok == {"count": 1, "inside": 1, "grounded": 1, "valid": True}
    assert not check_citations("Spikes attach it.", 84, TRANSCRIPT, SHOTS)["valid"]
    assert not check_citations("Spikes [00:05] and [09:00].", 84, TRANSCRIPT, SHOTS)["valid"]
    # Cited, inside, but nothing near 00:42 mentions spikes.
    assert not check_citations("Spikes [00:42].", 84, TRANSCRIPT, SHOTS)["valid"]
    assert check_citations("The logo says American Express [00:42].", 84, TRANSCRIPT, SHOTS)[
        "valid"
    ]


def test_summarize_and_gate():
    rows = [
        {"correct": True, "citations_valid": True, "task_type": "OCR", "duration": "short",
         "cost_usd": 0.5, "latency_s": 10.0, "baseline": "A", "answer": "A"},
        {"correct": False, "citations_valid": True, "task_type": "OCR", "duration": "long",
         "cost_usd": 0.3, "latency_s": 20.0, "baseline": "B", "answer": "A"},
    ]  # fmt: skip
    card = summarize(rows, accuracy_min=0.5, citation_min=0.9)
    assert card["accuracy"] == 0.5 and card["citation_rate"] == 1.0
    assert card["by_task_type"] == {"OCR": {"n": 2, "accuracy": 0.5}}
    assert card["by_length"]["long"] == {"n": 1, "accuracy": 0.0}
    assert card["cost_usd"] == {"total": 0.8, "mean": 0.4}
    assert card["baseline_accuracy"] == 0.5
    assert card["gate"]["passed"]
    assert not summarize(rows, accuracy_min=0.7, citation_min=0.9)["gate"]["passed"]
