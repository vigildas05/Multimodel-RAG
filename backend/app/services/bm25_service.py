"""Per-workspace in-memory BM25 keyword indexes.

Each workspace gets its own index, so BM25 statistics (IDF) and results never mix
tenants. Indexes are built lazily from that workspace's Qdrant points (the source of
truth) and cached with LRU eviction. Every cached index remembers the workspace's
`corpus_version` (a counter in Postgres bumped on each ingest/delete), and a search with
a newer version rebuilds it, so this also works when a separate worker process does the
ingestion.

rank_bm25 has no incremental update API, so a rebuild re-tokenizes the whole workspace.
That's fine at the tens-to-hundreds-of-documents per workspace this project targets; see
README for the production-scale alternative (Qdrant sparse vectors).
"""

import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

logger = logging.getLogger("app.services.bm25")

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class _WorkspaceIndex:
    version: int
    bm25: BM25Okapi | None
    payloads: list[dict]


class BM25Service:
    def __init__(self, max_workspaces: int = 128):
        self._max_workspaces = max_workspaces
        self._indexes: OrderedDict[str, _WorkspaceIndex] = OrderedDict()
        self._lock = threading.Lock()
        self._build_locks: dict[str, threading.Lock] = {}

    def search(
        self,
        workspace_id: str,
        corpus_version: int,
        query: str,
        top_k: int,
        load_corpus: Callable[[], list[dict]],
    ) -> list[dict]:
        """`load_corpus` returns the workspace's chunk payloads; called only on a cache miss
        or when `corpus_version` is newer than the cached index."""
        index = self._get_index(workspace_id, corpus_version, load_corpus)
        if index.bm25 is None:
            return []
        scores = index.bm25.get_scores(_tokenize(query))

        # No score>0 filter here: RRF fusion (in retrieval_service) only cares about rank
        # position, not score magnitude. BM25 idf can legitimately collapse to exactly 0
        # for terms appearing in exactly half a small corpus, which would otherwise wrongly
        # drop a chunk that's still the best (or only) keyword match.
        ranked = sorted(zip(index.payloads, scores, strict=False), key=lambda pair: pair[1], reverse=True)
        return [{"score": float(score), **payload} for payload, score in ranked[:top_k]]

    def invalidate(self, workspace_id: str) -> None:
        with self._lock:
            self._indexes.pop(workspace_id, None)

    def _get_index(self, workspace_id: str, version: int, load_corpus: Callable[[], list[dict]]) -> _WorkspaceIndex:
        with self._lock:
            cached = self._cached(workspace_id, version)
            if cached is not None:
                return cached
            build_lock = self._build_locks.setdefault(workspace_id, threading.Lock())

        # Build outside the global lock so one large workspace doesn't stall the others;
        # the per-workspace lock stops concurrent requests rebuilding the same index twice.
        with build_lock:
            with self._lock:
                cached = self._cached(workspace_id, version)
                if cached is not None:
                    return cached
            index = self._build(workspace_id, version, load_corpus())
            with self._lock:
                self._indexes[workspace_id] = index
                self._indexes.move_to_end(workspace_id)
                while len(self._indexes) > self._max_workspaces:
                    evicted, _ = self._indexes.popitem(last=False)
                    self._build_locks.pop(evicted, None)
            return index

    def _cached(self, workspace_id: str, version: int) -> _WorkspaceIndex | None:
        index = self._indexes.get(workspace_id)
        if index is None or index.version < version:
            return None
        self._indexes.move_to_end(workspace_id)
        return index

    @staticmethod
    def _build(workspace_id: str, version: int, payloads: list[dict]) -> _WorkspaceIndex:
        # Defense in depth: the loader is already workspace-scoped, but never index a chunk
        # that claims to belong to another workspace.
        foreign = [p for p in payloads if p.get("workspace_id") != workspace_id]
        if foreign:
            logger.error("Dropped %d foreign chunks while building BM25 for workspace %s", len(foreign), workspace_id)
            payloads = [p for p in payloads if p.get("workspace_id") == workspace_id]
        bm25 = BM25Okapi([_tokenize(p["text"]) for p in payloads]) if payloads else None
        logger.info("BM25 index for workspace %s rebuilt with %d chunks (v%d)", workspace_id, len(payloads), version)
        return _WorkspaceIndex(version=version, bm25=bm25, payloads=payloads)
