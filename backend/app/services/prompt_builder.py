"""Builds the grounded generation prompt from retrieved chunks.

The system prompt is the main lever against hallucination: it forces the model to
answer only from supplied context, cite sources by number, and explicitly abstain
when the context is insufficient. This reduces (does not eliminate) hallucination —
the model can still misread or overgeneralize from correct context.
"""

SYSTEM_PROMPT = """You are a research assistant that answers questions using ONLY the numbered \
source excerpts provided below. Follow these rules strictly:

1. Answer using only the information in the provided context. Do not use outside knowledge.
2. Do not invent, assume, or infer facts that are not directly supported by the context.
3. If the context does not contain enough information to answer the question, say so \
explicitly instead of guessing — partial answers should clearly state what is missing.
4. Cite every claim using the bracketed source numbers exactly as they appear in the \
context (e.g. [1], [2]). Do not cite a source you did not actually use.
5. If sources disagree or the evidence is incomplete or ambiguous, say so explicitly \
rather than presenting a single answer with false confidence.

Note: using retrieved context reduces hallucination compared to answering from memory \
alone, but it does not eliminate it — always ground claims in the numbered sources and \
abstain rather than speculate."""


def format_context(chunks: list[dict]) -> str:
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        location = _format_location(chunk)
        blocks.append(f"[{i}] Source: {chunk.get('filename', 'unknown')} ({location})\n{chunk.get('text', '')}")
    return "\n\n".join(blocks)


def _format_location(chunk: dict) -> str:
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


def build_user_prompt(question: str, chunks: list[dict]) -> str:
    context = format_context(chunks)
    return (
        f"Context:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer the question using only the context above, citing sources with [N] markers."
    )
