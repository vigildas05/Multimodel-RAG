"""Turns retrieved chunks into the security gateway's SourceBlocks (title + human-readable
location). The prompt itself — grounding rules, spotlighting, canary — is built by
security.prompt."""

from security.prompt import SourceBlock


def to_source_blocks(chunks: list[dict]) -> list[SourceBlock]:
    return [
        SourceBlock(
            index=i,
            title=chunk.get("filename", "unknown"),
            location=format_location(chunk),
            text=chunk.get("text", ""),
        )
        for i, chunk in enumerate(chunks, start=1)
    ]


def format_location(chunk: dict) -> str:
    if chunk.get("page_number") is not None:
        return f"Page {chunk['page_number']}"
    if chunk.get("audio_start_time") is not None:
        start = _format_timestamp(chunk["audio_start_time"])
        end = _format_timestamp(chunk["audio_end_time"]) if chunk.get("audio_end_time") is not None else "?"
        return f"{start}-{end}"
    return f"chunk {chunk.get('chunk_id', 'unknown')}"


def _format_timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
