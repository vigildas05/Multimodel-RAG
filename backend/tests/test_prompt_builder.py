from app.services.prompt_builder import build_user_prompt, format_context


def test_format_context_cites_page_number():
    chunks = [{"filename": "paper.pdf", "page_number": 14, "text": "Attention is all you need."}]
    context = format_context(chunks)
    assert "[1]" in context
    assert "paper.pdf" in context
    assert "Page 14" in context


def test_format_context_falls_back_to_chunk_id_without_page():
    chunks = [{"filename": "notes.txt", "chunk_id": "abc_0", "text": "some content"}]
    context = format_context(chunks)
    assert "chunk abc_0" in context
    assert "Page" not in context


def test_format_context_uses_timestamp_for_audio():
    chunks = [
        {
            "filename": "lecture.mp3",
            "audio_start_time": 65.0,
            "audio_end_time": 90.0,
            "text": "welcome to the lecture",
        }
    ]
    context = format_context(chunks)
    assert "01:05-01:30" in context


def test_build_user_prompt_includes_question_and_context():
    chunks = [{"filename": "doc.pdf", "page_number": 1, "text": "some fact"}]
    prompt = build_user_prompt("What is the fact?", chunks)
    assert "What is the fact?" in prompt
    assert "some fact" in prompt
    assert "[1]" in prompt
