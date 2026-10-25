"""Tenant isolation: user A must never retrieve, see, or affect user B's chunks.

Three layers:
1. Service level — VectorStoreService / BM25Service / RetrievalService against both an
   in-memory Qdrant and the real server, with a deterministic fake embedder (no models).
2. Structural — no back doors: nothing else imports Qdrant, every vector-store method
   requires a scope, routers never touch the vector store.
3. API level — crafted requests through the real app: guessed document IDs, smuggled
   workspace IDs, chat prompts, and deletes across tenants.
"""

import hashlib
import inspect
import re
import uuid
from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient

from app.core.exceptions import NoRelevantResultsError, VectorStoreUnavailableError
from app.core.tenancy import TenantScope
from app.models.chunk import Chunk
from app.services.bm25_service import BM25Service
from app.services.llm.base import LLMGenerationResult, LLMProvider
from app.services.retrieval_service import RetrievalService, RetrievalSettings
from app.services.vector_store_service import VectorStoreService
from helpers import signup, upload_pdf

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DIM = 64
SECRET_TEXT = "Project Nightingale launch codes are seven alpha nine. Nightingale is confidential."
OTHER_TEXT = "Quarterly gardening tips: water tomatoes early and mulch the beds."


# --- fakes ----------------------------------------------------------------------------


class HashingEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words are similar."""

    @staticmethod
    def _vec(text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def embed_chunks(self, texts):
        return np.stack([self._vec(t) for t in texts]) if texts else np.empty((0, DIM))

    def embed_query(self, query):
        return self._vec(query)


class PassthroughReranker:
    def rerank(self, query, candidates, top_n):
        for c in candidates:
            c["rerank_score"] = c.get("score", 0.0)
        return candidates[:top_n]


class Tenant:
    def __init__(self):
        self.scope = TenantScope(workspace_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
        self.version = 0


# --- service-level fixtures -----------------------------------------------------------


@pytest.fixture(params=["memory", "server"])
def store(request):
    if request.param == "memory":
        yield VectorStoreService("", 0, "isolation", DIM, client=QdrantClient(":memory:"))
        return
    try:
        s = VectorStoreService("localhost", 6333, "test_tenant_isolation", DIM)
    except VectorStoreUnavailableError:
        pytest.skip("Qdrant is not running on localhost:6333")
    s.reset_collection()
    yield s
    s.reset_collection()


@pytest.fixture
def world(store):
    """Two tenants, each with one document, plus a retrieval service over both."""
    embedder = HashingEmbedder()
    a, b = Tenant(), Tenant()
    tenants = {a.scope.workspace_id: a, b.scope.workspace_id: b}

    def add_doc(tenant: Tenant, text: str) -> str:
        doc_id = str(uuid.uuid4())
        chunk = Chunk(
            chunk_id=f"{doc_id}_0",
            document_id=doc_id,
            filename="doc.pdf",
            file_type="pdf",
            text=text,
            chunk_index=0,
            upload_timestamp="2026-01-01T00:00:00Z",
            embedding=embedder.embed_chunks([text])[0].tolist(),
        )
        store.upsert_chunks(tenant.scope, [chunk])
        tenant.version += 1
        return doc_id

    service = RetrievalService(
        embedder,
        store,
        BM25Service(),
        PassthroughReranker(),
        corpus_version=lambda wid: tenants[wid].version,
    )
    a.doc = add_doc(a, SECRET_TEXT)
    b.doc = add_doc(b, OTHER_TEXT)
    return {"store": store, "service": service, "a": a, "b": b, "add_doc": add_doc}


ALL_MODES = [
    RetrievalSettings(retrieval_top_k=10, final_context_chunks=10, use_hybrid_search=hybrid, use_reranking=rerank)
    for hybrid in (False, True)
    for rerank in (False, True)
]


# --- 1. service level -----------------------------------------------------------------


@pytest.mark.parametrize("settings", ALL_MODES, ids=lambda s: f"hybrid={s.use_hybrid_search}-rerank={s.use_reranking}")
def test_query_targeting_other_tenant_returns_only_own_chunks(world, settings):
    b = world["b"]
    result = world["service"].retrieve(b.scope, "Nightingale launch codes", settings)
    assert result.chunks, "B should still get its own (irrelevant) chunk back"
    assert all(c["workspace_id"] == b.scope.workspace_id for c in result.chunks)
    assert not any("Nightingale" in c["text"] for c in result.chunks)


@pytest.mark.parametrize("settings", ALL_MODES, ids=lambda s: f"hybrid={s.use_hybrid_search}-rerank={s.use_reranking}")
def test_guessed_document_id_from_other_tenant_yields_nothing(world, settings):
    b, a = world["b"], world["a"]
    settings = RetrievalSettings(**{**settings.__dict__, "document_ids": [a.doc]})
    with pytest.raises(NoRelevantResultsError):
        world["service"].retrieve(b.scope, "Nightingale", settings)


def test_empty_tenant_sees_nothing(world):
    stranger = Tenant()
    world["service"]._corpus_version = lambda wid: 0
    with pytest.raises(NoRelevantResultsError):
        world["service"].retrieve(stranger.scope, "Nightingale launch codes", ALL_MODES[-1])


def test_many_tenants_each_see_only_their_own_marker(store):
    embedder = HashingEmbedder()
    tenants = [Tenant() for _ in range(12)]
    for i, t in enumerate(tenants):
        text = f"marker{i} belongs to tenant number {i}"
        store.upsert_chunks(
            t.scope,
            [
                Chunk(
                    chunk_id=f"c{i}",
                    document_id=str(uuid.uuid4()),
                    filename="m.pdf",
                    file_type="pdf",
                    text=text,
                    chunk_index=0,
                    upload_timestamp="2026-01-01T00:00:00Z",
                    embedding=embedder.embed_chunks([text])[0].tolist(),
                )
            ],
        )
    service = RetrievalService(embedder, store, BM25Service(max_workspaces=4), PassthroughReranker(), lambda _: 1)
    settings = RetrievalSettings(retrieval_top_k=20, final_context_chunks=20, use_reranking=False)
    for i, t in enumerate(tenants):
        texts = [c["text"] for c in service.retrieve(t.scope, "marker belongs to tenant", settings).chunks]
        assert texts == [f"marker{i} belongs to tenant number {i}"]


def test_cross_tenant_reads_and_deletes_are_noops(world):
    store, a, b = world["store"], world["a"], world["b"]
    assert store.get_chunks_by_document(b.scope, a.doc) == []

    store.delete_by_document_id(b.scope, a.doc)
    store.delete_workspace(b.scope)

    remaining = store.get_chunks_by_document(a.scope, a.doc)
    assert len(remaining) == 1 and "Nightingale" in remaining[0]["text"]
    assert store.get_workspace_chunks(b.scope) == []


def test_payload_workspace_is_stamped_from_scope(world):
    for tenant in (world["a"], world["b"]):
        chunks = world["store"].get_workspace_chunks(tenant.scope)
        assert chunks and all(c["workspace_id"] == tenant.scope.workspace_id for c in chunks)


def test_bm25_index_refreshes_when_corpus_version_changes(world):
    a, service, store = world["a"], world["service"], world["store"]
    hybrid_only = RetrievalSettings(retrieval_top_k=5, final_context_chunks=5, use_reranking=False)
    assert service.retrieve(a.scope, "Nightingale", hybrid_only).metadata.bm25_candidate_count == 1

    store.delete_by_document_id(a.scope, a.doc)
    a.version += 1
    new_doc = world["add_doc"](a, "Kestrel replaces the old project entirely.")

    result = service.retrieve(a.scope, "Nightingale Kestrel", hybrid_only)
    assert [c["document_id"] for c in result.chunks] == [new_doc]


def test_bm25_drops_foreign_payloads_even_if_loader_leaks_them():
    mine, theirs = str(uuid.uuid4()), str(uuid.uuid4())
    leaky_corpus = [
        {"workspace_id": mine, "chunk_id": "m", "text": "ordinary notes"},
        {"workspace_id": theirs, "chunk_id": "t", "text": "Nightingale secret"},
    ]
    results = BM25Service().search(mine, 1, "Nightingale secret", 10, load_corpus=lambda: leaky_corpus)
    assert [r["chunk_id"] for r in results] == ["m"]


def test_bm25_cache_is_keyed_per_workspace():
    bm25 = BM25Service()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    corpora = {
        a: [{"workspace_id": a, "chunk_id": "a1", "text": "alpha secret"}],
        b: [{"workspace_id": b, "chunk_id": "b1", "text": "beta notes"}],
    }
    for wid in (a, b, a, b):
        results = bm25.search(wid, 1, "alpha beta secret notes", 10, load_corpus=lambda w=wid: corpora[w])
        assert all(r["workspace_id"] == wid for r in results)


# --- 2. structural: no back doors -----------------------------------------------------


def test_only_the_vector_store_service_imports_qdrant():
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if re.search(r"^\s*(from|import)\s+qdrant_client", path.read_text(encoding="utf-8"), re.M)
        and path.name != "vector_store_service.py"
    ]
    assert offenders == [], f"Qdrant must only be used via VectorStoreService: {offenders}"


def test_every_vector_store_operation_requires_a_scope():
    unscoped_allowed = {"reset_collection"}  # test/eval setup only, see its docstring
    for name, method in inspect.getmembers(VectorStoreService, inspect.isfunction):
        if name.startswith("_") or name in unscoped_allowed:
            continue
        params = list(inspect.signature(method).parameters.values())
        assert len(params) > 1 and params[1].name == "scope" and params[1].annotation is TenantScope, name
        assert params[1].default is inspect.Parameter.empty, f"{name}: scope must be required"


def test_retrieval_requires_a_scope():
    params = list(inspect.signature(RetrievalService.retrieve).parameters.values())
    assert params[1].name == "scope" and params[1].annotation is TenantScope


def test_routers_never_touch_the_vector_store_directly():
    for path in (APP_DIR / "routers").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "vector_store" not in source and "VectorStoreService" not in source, path.name


# --- 3. API level ---------------------------------------------------------------------


class RecordingLLM(LLMProvider):
    def __init__(self):
        self.prompts: list[str] = []

    async def generate(self, system_prompt, user_prompt, temperature, top_p, max_output_tokens):
        self.prompts.append(system_prompt + "\n" + user_prompt)
        return LLMGenerationResult(text="Answer based on the context [1].")

    async def describe_image(self, image_bytes, prompt, temperature, max_output_tokens):
        return ""

    async def health_check(self):
        return True


@pytest.fixture
def tenants(client_factory):
    alice, bob = client_factory(), client_factory()
    alice.user = signup(alice)
    bob.user = signup(bob)
    alice.doc = upload_pdf(alice, SECRET_TEXT, "nightingale.pdf")["document_id"]
    bob.doc = upload_pdf(bob, OTHER_TEXT, "garden.pdf")["document_id"]
    yield alice, bob
    for c in (alice, bob):
        c.post("/documents/reset")


def _texts(resp) -> list[str]:
    return [c["text"] for c in resp.json()["chunks"]]


def test_api_search_never_returns_other_tenants_chunks(tenants):
    _, bob = tenants
    for hybrid in (True, False):
        resp = bob.post(
            "/search",
            json={"query": "Nightingale launch codes", "settings": {"use_hybrid_search": hybrid}},
        )
        assert resp.status_code == 200
        assert _texts(resp) and not any("Nightingale" in t for t in _texts(resp))


def test_api_search_with_guessed_document_ids_is_404(tenants):
    alice, bob = tenants
    for ids in ([alice.doc], [bob.doc, alice.doc], ["not-a-uuid"], [str(uuid.uuid4())]):
        resp = bob.post("/search", json={"query": "Nightingale", "settings": {"document_ids": ids}})
        assert resp.status_code == 404, ids
        assert resp.json()["error_type"] == "document_not_found"
    # Own document IDs still work.
    own = bob.post("/search", json={"query": "tomatoes", "settings": {"document_ids": [bob.doc]}})
    assert own.status_code == 200


@pytest.mark.parametrize(
    "make_body",
    [
        lambda ws: {"query": "Nightingale", "workspace_id": ws},
        lambda ws: {"query": "Nightingale", "settings": {"workspace_id": ws}},
        lambda ws: {"query": "Nightingale", "settings": {"tenant": ws, "use_reranking": False}},
    ],
    ids=["top-level", "in-settings", "alias-in-settings"],
)
def test_api_rejects_smuggled_workspace_fields(tenants, make_body):
    alice, bob = tenants
    body = make_body(alice.user["workspace_id"])
    assert bob.post("/search", json=body).status_code == 422

    chat_body = {("question" if k == "query" else k): v for k, v in body.items()}
    assert bob.post("/chat", json=chat_body).status_code == 422
    # Control: the same body without the smuggled field is accepted.
    assert bob.post("/search", json={"query": "Nightingale"}).status_code == 200


def test_api_ignores_workspace_query_params_and_headers(tenants):
    alice, bob = tenants
    resp = bob.post(
        f"/search?workspace_id={alice.user['workspace_id']}",
        json={"query": "Nightingale launch codes"},
        headers={"X-Workspace-Id": alice.user["workspace_id"]},
    )
    assert resp.status_code == 200
    assert not any("Nightingale" in t for t in _texts(resp))


def test_api_chat_prompt_contains_no_other_tenant_content(tenants, app, monkeypatch):
    _, bob = tenants
    llm = RecordingLLM()
    monkeypatch.setattr(app.state, "llm_provider", llm)

    resp = bob.post("/chat", json={"question": "What are the Nightingale launch codes?"})
    assert resp.status_code == 200
    assert llm.prompts and not any("seven alpha nine" in p for p in llm.prompts)
    assert all(s["document"] == "garden.pdf" for s in resp.json()["sources"])


def test_api_cross_tenant_chunk_read_and_delete_fail(tenants):
    alice, bob = tenants
    assert bob.get(f"/documents/{alice.doc}/chunks").status_code == 404
    assert bob.delete(f"/documents/{alice.doc}").status_code == 404
    assert bob.post("/documents/reset").json()["documents_removed"] == 1  # only Bob's own

    chunks = alice.get(f"/documents/{alice.doc}/chunks")
    assert chunks.status_code == 200 and "Nightingale" in chunks.json()["chunks"][0]["text"]
    assert any("Nightingale" in t for t in _texts(alice.post("/search", json={"query": "Nightingale"})))


def test_api_deleted_document_disappears_from_hybrid_search(tenants):
    alice, _ = tenants
    assert any("Nightingale" in t for t in _texts(alice.post("/search", json={"query": "Nightingale"})))
    assert alice.delete(f"/documents/{alice.doc}").status_code == 200
    assert alice.post("/search", json={"query": "Nightingale"}).status_code == 404
