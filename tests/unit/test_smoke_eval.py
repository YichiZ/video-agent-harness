from evals.smoke import load_questions, parse_answer, question_text


def test_parse_answer_takes_the_last_answer_line():
    assert parse_answer("Answer: B is tempting...\n\nAnswer: **C**") == "C"
    assert parse_answer("Answer: `A`") == "A"
    assert parse_answer("I think it's D.") is None


def test_smoke_questions_ask_for_the_answer_format():
    questions = load_questions()
    assert len(questions) == 6 and {q["duration"] for q in questions} == {"short", "medium"}
    text = question_text(questions[0])
    assert questions[0]["options"][0] in text and "Answer: <letter>" in text
