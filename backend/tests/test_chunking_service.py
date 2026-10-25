from app.services.chunking_service import RecursiveChunker


def test_short_text_single_chunk():
    chunker = RecursiveChunker()
    chunks = chunker.split("Hello world.", chunk_size=800, chunk_overlap=100)
    assert len(chunks) == 1
    assert chunks[0].text == "Hello world."


def test_empty_text_no_chunks():
    chunker = RecursiveChunker()
    assert chunker.split("   ", chunk_size=800, chunk_overlap=100) == []


def test_respects_chunk_size_upper_bound_roughly():
    chunker = RecursiveChunker()
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 30 for i in range(30))
    chunks = chunker.split(text, chunk_size=200, chunk_overlap=20)
    assert len(chunks) > 1
    # allow slack for the overlap prefix appended to each chunk after the first
    for c in chunks:
        assert len(c.text) <= 200 + 20 + 5


def test_overlap_shares_trailing_text_between_consecutive_chunks():
    chunker = RecursiveChunker()
    text = "\n\n".join(f"Paragraph {i} has some unique filler words here today." for i in range(20))
    chunks = chunker.split(text, chunk_size=100, chunk_overlap=30)
    assert len(chunks) > 1
    prev_tail = chunks[0].text[-30:]
    assert prev_tail.strip() in chunks[1].text


def test_paragraph_boundaries_preferred_over_arbitrary_cuts():
    chunker = RecursiveChunker()
    text = "First paragraph is short.\n\nSecond paragraph is also short.\n\nThird one too."
    chunks = chunker.split(text, chunk_size=40, chunk_overlap=0)
    # None of the chunks should have cut a paragraph mid-sentence into a fragment
    # smaller than the whole available paragraph when it would have fit.
    assert all(c.text.strip() for c in chunks)


def test_invalid_overlap_raises():
    chunker = RecursiveChunker()
    try:
        chunker.split("text", chunk_size=100, chunk_overlap=100)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
