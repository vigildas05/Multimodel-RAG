from app.services.prompt_builder import format_location, to_source_blocks


def test_location_uses_page_number():
    assert format_location({"page_number": 14}) == "Page 14"


def test_location_falls_back_to_chunk_id():
    assert format_location({"chunk_id": "abc_0"}) == "chunk abc_0"


def test_location_uses_timestamp_for_audio():
    assert format_location({"audio_start_time": 65.0, "audio_end_time": 90.0}) == "01:05-01:30"


def test_source_blocks_are_numbered_from_one():
    blocks = to_source_blocks([{"filename": "a.pdf", "page_number": 1, "text": "x"}, {"filename": "b.pdf", "text": "y"}])
    assert [(b.index, b.title, b.text) for b in blocks] == [(1, "a.pdf", "x"), (2, "b.pdf", "y")]
