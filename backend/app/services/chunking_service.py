"""Recursive text chunking.

Recursive chunking tries a list of separators from "largest structural unit" to
"smallest" (paragraphs -> lines -> sentences -> words -> characters), splitting on
the first separator that gets each piece under chunk_size, and only falling back to
a smaller separator when a piece is still too big. This keeps semantically related
text (a paragraph, a sentence) together whenever possible, instead of blindly cutting
every N characters and severing a sentence or word mid-way.
"""

from dataclasses import dataclass

DEFAULT_SEPARATORS: list[str] = ["\n\n", "\n", ". ", " ", ""]


@dataclass
class TextChunk:
    text: str
    chunk_index: int
    start_offset: int


class RecursiveChunker:
    def __init__(self, separators: list[str] | None = None):
        self.separators = separators or DEFAULT_SEPARATORS

    def split(self, text: str, chunk_size: int, chunk_overlap: int) -> list[TextChunk]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be >= 0 and < chunk_size")

        text = text.strip()
        if not text:
            return []

        raw_pieces = self._recursive_split(text, chunk_size, self.separators)
        merged = self._merge_with_overlap(raw_pieces, chunk_size, chunk_overlap)

        chunks: list[TextChunk] = []
        search_from = 0
        for i, piece in enumerate(merged):
            offset = text.find(piece[:50], search_from) if piece else search_from
            if offset == -1:
                offset = search_from
            chunks.append(TextChunk(text=piece, chunk_index=i, start_offset=offset))
            search_from = max(search_from, offset)
        return chunks

    def _recursive_split(self, text: str, chunk_size: int, separators: list[str]) -> list[str]:
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        if not separators:
            # Base case: no separator left, hard-cut by character count.
            return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

        sep, rest_separators = separators[0], separators[1:]
        if sep == "":
            return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

        parts = text.split(sep)
        results: list[str] = []
        buffer = ""
        for part in parts:
            candidate = (buffer + sep + part) if buffer else part
            if len(candidate) <= chunk_size:
                buffer = candidate
            else:
                if buffer:
                    results.append(buffer)
                if len(part) > chunk_size:
                    results.extend(self._recursive_split(part, chunk_size, rest_separators))
                    buffer = ""
                else:
                    buffer = part
        if buffer:
            results.append(buffer)
        return [r for r in results if r.strip()]

    def _merge_with_overlap(self, pieces: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
        """Re-pack small pieces up toward chunk_size, then apply trailing overlap between
        consecutive chunks so context isn't lost at a boundary."""
        if not pieces:
            return []

        packed: list[str] = []
        buffer = ""
        for piece in pieces:
            candidate = f"{buffer} {piece}".strip() if buffer else piece
            if len(candidate) <= chunk_size:
                buffer = candidate
            else:
                if buffer:
                    packed.append(buffer)
                buffer = piece
        if buffer:
            packed.append(buffer)

        if chunk_overlap == 0 or len(packed) <= 1:
            return packed

        overlapped: list[str] = [packed[0]]
        for i in range(1, len(packed)):
            prev = packed[i - 1]
            overlap_text = prev[-chunk_overlap:] if len(prev) > chunk_overlap else prev
            combined = f"{overlap_text} {packed[i]}".strip()
            overlapped.append(combined)
        return overlapped
