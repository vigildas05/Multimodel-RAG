"""In-memory BM25 keyword index.

rank_bm25 has no incremental update API, so the index is fully rebuilt whenever the
corpus changes (document add/delete) or on server startup (from Qdrant, the source of
truth). This is a deliberate MVP simplification — fine at the tens-to-hundreds-of-
documents scale this project targets; see README for the production-scale alternative
(Qdrant sparse vectors / OpenSearch) that avoids full rebuilds.
"""

import logging
import re
import threading

from rank_bm25 import BM25Okapi

logger = logging.getLogger("app.services.bm25")

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Service:
    def __init__(self):
        self._lock = threading.Lock()
        self._bm25: BM25Okapi | None = None
        self._corpus_payloads: list[dict] = []

    def rebuild(self, chunk_payloads: list[dict]) -> None:
        with self._lock:
            self._corpus_payloads = chunk_payloads
            if not chunk_payloads:
                self._bm25 = None
                return
            tokenized = [_tokenize(c["text"]) for c in chunk_payloads]
            self._bm25 = BM25Okapi(tokenized)
        logger.info("BM25 index rebuilt with %d chunks", len(chunk_payloads))

    def search(self, query: str, top_k: int) -> list[dict]:
        with self._lock:
            if self._bm25 is None or not self._corpus_payloads:
                return []
            scores = self._bm25.get_scores(_tokenize(query))
            payloads = self._corpus_payloads

        # No score>0 filter here: RRF fusion (in retrieval_service) only cares about rank
        # position, not score magnitude. BM25 idf can legitimately collapse to exactly 0
        # for terms appearing in exactly half a small corpus, which would otherwise wrongly
        # drop a chunk that's still the best (or only) keyword match.
        ranked = sorted(zip(payloads, scores), key=lambda pair: pair[1], reverse=True)
        return [{"score": float(score), **payload} for payload, score in ranked[:top_k]]
