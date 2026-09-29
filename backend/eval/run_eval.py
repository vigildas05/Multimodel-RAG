"""Retrieval evaluation harness.

Ingests the small hand-authored corpus in eval_dataset.json into a dedicated Qdrant
collection (kept separate from the app's main `document_chunks` collection so this
never disturbs real uploaded data), runs every question through the real retrieval
pipeline (embedding -> Qdrant -> optional BM25/RRF -> optional reranking), and reports
real Recall@K, Precision@K, and MRR computed from those actual results.

No numbers here are fabricated — if Qdrant isn't running, this script fails loudly
instead of printing placeholder metrics.

Usage (from backend/):
    python eval/run_eval.py
    python eval/run_eval.py --top-k 5 --final-k 3 --no-rerank --no-hybrid
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.chunk import Chunk  # noqa: E402
from app.services.bm25_service import BM25Service  # noqa: E402
from app.services.chunking_service import RecursiveChunker  # noqa: E402
from app.services.embedding_service import EmbeddingService  # noqa: E402
from app.services.reranking_service import RerankingService  # noqa: E402
from app.services.retrieval_service import RetrievalService, RetrievalSettings  # noqa: E402
from app.services.vector_store_service import VectorStoreService  # noqa: E402

EVAL_COLLECTION = "eval_document_chunks"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL = "BAAI/bge-reranker-base"


def load_dataset() -> dict:
    path = Path(__file__).parent / "eval_dataset.json"
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_corpus(dataset: dict, embedder: EmbeddingService, store: VectorStoreService, chunker: RecursiveChunker):
    store.reset_collection()
    all_chunks: list[Chunk] = []
    for doc in dataset["corpus"]:
        text_chunks = chunker.split(doc["text"], chunk_size=400, chunk_overlap=50)
        for i, tc in enumerate(text_chunks):
            all_chunks.append(
                Chunk(
                    chunk_id=f"{doc['doc_id']}_{i}",
                    document_id=doc["doc_id"],
                    filename=doc["filename"],
                    file_type="pdf",
                    text=tc.text,
                    chunk_index=i,
                    upload_timestamp=datetime.now(timezone.utc).isoformat(),
                    page_number=1,
                )
            )
    vectors = embedder.embed_chunks([c.text for c in all_chunks])
    for chunk, vec in zip(all_chunks, vectors):
        chunk.embedding = vec.tolist()
    store.upsert_chunks(all_chunks)
    return all_chunks


def recall_at_k(retrieved_doc_ids: list[str], expected_doc_id: str) -> float:
    return 1.0 if expected_doc_id in retrieved_doc_ids else 0.0


def precision_at_k(retrieved_doc_ids: list[str], expected_doc_id: str) -> float:
    if not retrieved_doc_ids:
        return 0.0
    relevant = sum(1 for d in retrieved_doc_ids if d == expected_doc_id)
    return relevant / len(retrieved_doc_ids)


def reciprocal_rank(retrieved_doc_ids: list[str], expected_doc_id: str) -> float:
    for i, d in enumerate(retrieved_doc_ids, start=1):
        if d == expected_doc_id:
            return 1.0 / i
    return 0.0


def main():
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality against a real Qdrant instance.")
    parser.add_argument("--top-k", type=int, default=5, help="retrieval_top_k for the eval run")
    parser.add_argument("--final-k", type=int, default=3, help="final_context_chunks for the eval run")
    parser.add_argument("--no-rerank", action="store_true", help="disable reranking")
    parser.add_argument("--no-hybrid", action="store_true", help="disable hybrid (BM25) search")
    args = parser.parse_args()

    print("Loading models (this may take a minute on first run)...")
    embedder = EmbeddingService.get_instance(EMBEDDING_MODEL, device="cpu")
    reranker = RerankingService.get_instance(RERANKER_MODEL, device="cpu")
    store = VectorStoreService("localhost", 6333, EVAL_COLLECTION, embedder.dimension)
    bm25 = BM25Service()
    chunker = RecursiveChunker()

    dataset = load_dataset()
    print(f"Ingesting {len(dataset['corpus'])} documents into eval collection '{EVAL_COLLECTION}'...")
    ingest_corpus(dataset, embedder, store, chunker)
    bm25.rebuild(store.get_all_chunks())

    retrieval_service = RetrievalService(embedder, store, bm25, reranker, rrf_k=60)
    settings = RetrievalSettings(
        retrieval_top_k=args.top_k,
        final_context_chunks=args.final_k,
        use_hybrid_search=not args.no_hybrid,
        use_reranking=not args.no_rerank,
    )

    print(
        f"\nRunning {len(dataset['questions'])} questions "
        f"(top_k={args.top_k}, final_k={args.final_k}, "
        f"hybrid={settings.use_hybrid_search}, rerank={settings.use_reranking})\n"
    )

    recalls, precisions, rrs = [], [], []
    rows = []
    for q in dataset["questions"]:
        try:
            result = retrieval_service.retrieve(q["question"], settings)
            retrieved_doc_ids = [c["document_id"] for c in result.chunks]
        except Exception:
            retrieved_doc_ids = []

        r = recall_at_k(retrieved_doc_ids, q["expected_document_id"])
        p = precision_at_k(retrieved_doc_ids, q["expected_document_id"])
        rr = reciprocal_rank(retrieved_doc_ids, q["expected_document_id"])
        recalls.append(r)
        precisions.append(p)
        rrs.append(rr)
        rows.append((q["question"][:60], q["expected_document_id"], r, p, rr))

    print(f"{'Question':62} {'Expected':18} {'Recall':>7} {'Prec':>6} {'RR':>6}")
    print("-" * 100)
    for question, expected, r, p, rr in rows:
        print(f"{question:62} {expected:18} {r:7.2f} {p:6.2f} {rr:6.2f}")

    print("-" * 100)
    n = len(dataset["questions"])
    print(f"\nRecall@{args.final_k}:    {sum(recalls) / n:.3f}")
    print(f"Precision@{args.final_k}: {sum(precisions) / n:.3f}")
    print(f"MRR:            {sum(rrs) / n:.3f}")

    print(
        "\nNote: these are retrieval metrics computed from real Qdrant search results "
        "over the corpus in eval_dataset.json. They measure whether the correct SOURCE "
        "DOCUMENT was retrieved for each question — not whether the final LLM answer was "
        "factually correct. See README.md 'Evaluation' section for how generation-quality "
        "metrics (faithfulness, answer relevance, context relevance) relate to this and why "
        "they require an LLM-as-judge setup this MVP does not automate."
    )


if __name__ == "__main__":
    main()
