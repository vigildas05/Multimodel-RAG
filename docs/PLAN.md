# Sentinel RAG — Security-first MVP plan

Scope of this milestone: **Phase 1 (accounts + multi-tenancy), Phase 2 (upload/ingestion
security), Phase 4 (LLM security gateway), Phase 5 (red-team suite), and a basic deploy.**
Credits/Razorpay (Phase 3), the admin dashboard (Phase 6) and full CI/CD + observability
(Phase 8) come after. A minimal CI workflow (tests + red-team gate) is included now because
Phase 5 requires it.

---

## 1. What exists today

**Backend (FastAPI, ~2.5k lines).** Clean router → service layering, all heavy objects built
once in `main.py`'s lifespan and handed out via `dependencies.py`.

| Area | Where | Notes |
|---|---|---|
| Ingestion | `services/document_service.py` | PDF (PyMuPDF, scanned-page → PaddleOCR), image (OCR + vision-LLM description), audio (faster-whisper, segment packing with timestamps). Runs **inline in the upload request**. |
| Chunking | `services/chunking_service.py` | Recursive splitter, size/overlap from config. |
| Embeddings | `services/embedding_service.py` | `bge-small-en-v1.5` (384-d), bge query prefix, singleton. |
| Vector store | `services/vector_store_service.py` | One Qdrant collection, payload indexes on `document_id`, `file_type`. |
| Keyword search | `services/bm25_service.py` | **One global in-memory BM25 index** over every chunk, rebuilt from `get_all_chunks()`. |
| Retrieval | `services/retrieval_service.py` | Dense + BM25 → RRF → threshold → `bge-reranker-base` cross-encoder. |
| Prompting | `services/prompt_builder.py` | Grounding system prompt, `[N]` numbered context blocks. |
| LLM | `services/llm/` | `LLMProvider` ABC, working Ollama provider, Groq stub. |
| Metadata | `storage/document_registry.py` | SQLite, documents table only. |
| Validation | `core/file_validation.py` | Extension allow-list, size limit, filename sanitizing (no magic-byte check). |
| Tests | `backend/tests/` | Chunking, prompt builder, retrieval, embedding; Qdrant tests skip if no Qdrant. |
| Eval | `backend/eval/run_eval.py` | Real Recall@K / Precision@K / MRR on a small hand-made set. |

**Frontend (React 18 + Vite + Tailwind, JSX).** Single page: sidebar (documents, upload),
chat area, settings panel. `react-markdown` v9 renders answers (raw HTML is already *not*
rendered — good baseline). Axios client with no auth.

**Docker.** `docker-compose.yml` with Qdrant + backend; Ollama on the host. Single-stage
Dockerfile running as root.

### Gaps that matter for this milestone

1. **No tenant concept anywhere.** Qdrant points have no `workspace_id`; every endpoint can
   see and delete every document; `/documents/reset` wipes everyone.
2. **The global BM25 index is a cross-tenant leak path.** It searches the whole corpus and
   filters by `document_ids` *afterwards*. Even with filtering, other tenants' chunks crowd
   out results; without it, they leak. It must become per-workspace.
3. **Heavy work in the request path.** OCR/Whisper/vision run inside the upload request
   (and in `/audio/transcribe`, `/images/analyze`), so one large or malicious file can tie up
   the API.
4. **Untrusted text flows straight into the prompt.** No delimiting of context as data, no
   input/output checks. The vision-LLM description of an uploaded image is a second-order
   injection channel (image text → vision model → chunk → answer model).
5. **Info leaks.** The catch-all exception handler returns `str(exc)` to the client; CORS
   allows `*` methods/headers with credentials.

The retrieval algorithm itself (RRF, reranking, prefixes, chunking) needs no change; tenancy
is added *around* it.

---

## 2. Key design decisions

### 2.1 Database
- PostgreSQL via **SQLAlchemy 2.0 (sync, psycopg 3)** + **Alembic**. Sync matches the existing
  `run_in_threadpool` style; no async rewrite of services.
- MVP tables: `users`, `refresh_tokens`, `workspaces`, `workspace_members`, `documents`,
  `chunk_flags` (quarantine), `query_logs`, `security_events`, `audit_log`, `usage_quota`.
  `credit_ledger` / `payments` / `webhook_events` arrive with Phase 3 as their own migration.
- `DocumentRegistry` (SQLite) is replaced by a repository with the same method names, so
  `DocumentService` changes minimally.

### 2.2 Tenant isolation — one choke point
- One Qdrant collection (`chunks_v2`), `workspace_id` payload index created with
  `is_tenant=True`.
- New `TenantScope` value object (`workspace_id`, `user_id`, `role`) is created **only** by the
  auth dependency from the verified JWT — never from request bodies.
- `VectorStoreService` methods all take a required `scope: TenantScope` and always add a
  `must: workspace_id == scope.workspace_id` condition. There is no unscoped search/scroll
  method; `get_all_chunks()` is removed.
- `RetrievalService.retrieve(scope, query, settings)` is the only query entry point; routers
  never touch Qdrant. A test greps the codebase to assert no other module imports
  `QdrantClient`.
- Client-supplied `document_ids` are intersected with the workspace's documents from Postgres
  before use; unknown IDs → 404 (not 403, to avoid confirming existence).
- **BM25 becomes per-workspace**: `{workspace_id: index}` with LRU eviction, built lazily from
  that workspace's Qdrant points. Since the worker writes and the API reads, each workspace has
  a `corpus_version` in Postgres; the API rebuilds when its cached version is stale.
- Isolation tests (run against Qdrant in-memory mode and real Qdrant in CI): user A vs user B
  with guessed document IDs, forged `workspace_id` fields in JSON, other users' chunk IDs on
  `/documents/{id}/chunks`, delete of another tenant's doc, BM25-only and dense-only paths.

### 2.3 Auth
- argon2id (`argon2-cffi`), email + password, `user` / `admin` roles.
- Access JWT (15 min) + opaque refresh token (7 days) stored **hashed** in Postgres, rotated on
  every refresh, with reuse detection (a reused old token revokes the whole token family).
- Both in `httpOnly; Secure; SameSite` cookies. Because cookies make CSRF possible, state-changing
  requests also need a double-submit CSRF header.
- Roles enforced by FastAPI dependencies (`require_user`, `require_admin`) on every router; a test
  walks `app.routes` and fails if any non-public route lacks an auth dependency.

### 2.4 Demo account
Each "Try the demo" click creates an **ephemeral guest workspace** pre-seeded by copying the sample
documents' Qdrant points (no re-embedding), with a small daily query quota and a 24h TTL cleanup job.
A shared writable demo workspace would let one visitor plant a poisoned document for the next, which
is itself a prompt-injection hole. Until credits exist, quotas are enforced by a simple
`usage_quota` counter.

### 2.5 Worker and ingestion security
- **arq** (async, Redis-backed) worker process; the API only stores the file, writes a `queued`
  document row, and enqueues a job. The frontend already polls document status.
- Untrusted parsing (PDF open/page count, image decode/dimensions, audio probe) runs in a **killable
  subprocess** with a hard timeout and memory cap. A malicious PDF can hang a C library in a way
  `asyncio` timeouts can't interrupt; a subprocess can always be killed.
- Model inference (OCR, Whisper, embeddings, ingest-time injection scan) runs in the worker with
  lazily loaded models. The API process loads only what queries need: embedder, reranker, and the
  injection classifier.
- Magic-byte validation (`filetype`/signature checks + a structural open in the sandbox), with
  env-configurable limits on size, pages, pixels, duration, and documents per workspace. Uploads
  go to `DATA_DIR/uploads/<workspace_id>/<uuid>` with no user-controlled path components; the
  original filename is stored only as display metadata.
- Hidden-PDF-content detection via PyMuPDF `page.get_texttrace()`: invisible render mode (3),
  opacity ≈ 0, near-white fill, font size < threshold, bbox outside or clipped from `page.rect`.
  Hidden spans are **stripped from indexed text** and recorded as a security event that the user
  can see. Limitation: "white on white" assumes a white background; coloured backgrounds aren't
  checked in the MVP.

### 2.6 LLM security gateway — `backend/security/`
A standalone package with no imports from `app.*`, so it reads as a reusable gateway:

```
security/
  gateway.py          # SecurityGateway: scan_input / scan_chunk / build_prompt / scan_output
  detectors/
    heuristics.py     # override phrases, role-play, extraction, encodings, homoglyphs, zero-width, length
    normalize.py      # NFKC, confusables map, zero-width strip, base64/hex decode-and-rescan
    classifier.py     # HF model wrapper, loaded once, CPU, truncation/windowing
  scoring.py          # combine into risk score, thresholds, decision + reasons
  prompt.py           # spotlighting: <document id=… > data blocks, canary, hierarchy rules
  output/
    canary.py  pii.py  citations.py
  events.py           # SecurityEvent dataclass + sink protocol (Postgres sink lives in app/)
```

- Classifier default: **`protectai/deberta-v3-base-prompt-injection-v2`** (Apache-2.0, not gated).
  Meta Prompt Guard is a configurable alternative, but it's gated on Hugging Face (needs a license
  acceptance + HF token).
- Spotlighting: each chunk is wrapped in a delimited block with a per-request random boundary, and
  boundary-like strings are escaped inside chunk text so a document can't "close" its own block.
- Canary: a random per-request token in the system prompt; if it shows up in the output, the
  response is blocked and a `prompt_leak` event is logged.
- PII redaction: email, Indian/E.164 phone numbers, Aadhaar (12 digits, **Verhoeff checksum**), PAN
  (`AAAAA9999A`), card numbers (**Luhn**). The checksums keep false positives down.
- Citation verification: `[N]` markers outside `1..len(chunks)` are stripped and flagged.
- App security: Redis-backed rate limits per user and per IP; strict CORS; CSP/HSTS/nosniff/
  `frame-ancestors 'none'` middleware; request body limits; generic 500s (no `str(exc)` to the client).

### 2.7 Red-team suite — `redteam/`
- `datasets/attacks.jsonl` (≥300 across the 6 categories) + `datasets/benign.jsonl`. Each record has
  `id`, `category`, `vector` (`query` | `document`), `payload`, and a **success predicate** (e.g.
  output contains canary, contains planted PII, contains the attack's target string).
- **Mock LLM = deliberately gullible model**: it obeys any instruction it can see in its input and
  echoes context. With the gateway OFF, ASR is therefore ~100% by construction. With it ON, the number
  measures how much the guards catch against a worst-case model. Both runs are real measurements; the
  report states plainly that mock ASR ≠ real-model ASR. `--real-llm` runs against the configured
  provider.
- Prompts: a mix I write by hand, templated variants, and samples from permissively licensed public
  sets (licences recorded per record). A **held-out split** is never used while tuning the heuristics,
  so the reported numbers aren't just overfitting. Overlap with the classifier's training data will
  be noted as a limitation.
- Outputs: `reports/latest.json` + `reports/latest.md`, overall and per category (ASR, block rate,
  FPR, p50/p95 added latency), plus an `--ab` mode that runs OFF vs ON.

### 2.8 Basic deploy
- Multi-stage, non-root images: `api`, `worker` (shared base), `caddy`.
- VM runs `caddy + api + worker + redis` via Compose. Redis stays **on the VM, not Upstash**: arq polls
  Redis constantly and would burn through Upstash's free command quota.
- Postgres → Neon, Qdrant → Qdrant Cloud free tier, frontend → Vercel, LLM → OpenAI-compatible provider
  (Groq / Gemini / NIM) via a new `OpenAICompatibleProvider` that replaces the Groq stub.
- **Cookie domain:** httpOnly cookies across `*.vercel.app` → `api.duckdns.org` are third-party and get
  blocked by Safari and increasingly by Chrome. Recommended: one domain with `app.` (Vercel) and `api.`
  (VM) subdomains; the GitHub Student Pack includes a free domain. Fallback: a Vercel rewrite proxying
  `/api/*` to the VM.
- **Oracle ARM risk:** PaddlePaddle's aarch64 Linux support is spotty. If it won't install there, the plan
  is to switch OCR to `rapidocr-onnxruntime` (same PaddleOCR models exported to ONNX), behind the existing
  `OCRService` interface. I'll ask before making that switch. An x86 VM (Student Pack credits) avoids it.

---

## 3. Execution order (one commit per working step, tests alongside)

| # | Step | Done when |
|---|---|---|
| 1.1 | Postgres + SQLAlchemy models + Alembic; Compose adds `postgres`, `redis` | `alembic upgrade head` works; registry tests pass on Postgres |
| 1.2 | Auth: register/login/refresh/logout, argon2, JWT + rotating refresh, CSRF, roles | auth tests incl. refresh-reuse detection |
| 1.3 | Tenant scope through Qdrant, BM25, routers; `chunks_v2` collection | **isolation test suite green** |
| 1.4 | Frontend: router, login/register, auth context, `withCredentials` + CSRF | manual run-through |
| 1.5 | Demo guest workspaces + seed script + quota | demo login → query works |
| 2.1 | arq worker, enqueue on upload, status polling, sandboxed parsing w/ timeouts | killed-on-timeout test with a crafted slow file |
| 2.2 | Magic bytes + all limits + safe storage paths | validation tests (polyglots, renamed files, oversize) |
| 2.3 | Hidden PDF content detection/stripping | tests with generated white/tiny/off-page/invisible text PDFs |
| 4.1 | `security/` heuristics + normalization | unit tests per detector |
| 4.2 | Classifier stage + scoring + input blocking + events | tests with the model stubbed; one slow test with the real model |
| 4.3 | Ingest guard + quarantine + UI showing quarantined chunks | quarantined chunks excluded from retrieval (tested) |
| 4.4 | Spotlighting prompt + canary + output guard (canary, PII, citations) | unit tests |
| 4.5 | App security: rate limits, headers, CORS, size limits, error hygiene | middleware tests |
| 5.1 | Red-team datasets + eval runner + mock LLM + A/B + reports | `python -m redteam.eval --ab` produces both reports |
| 5.2 | GitHub Actions: ruff, pytest (Postgres/Qdrant/Redis services), red-team ASR gate | green on a PR |
| D.1 | `OpenAICompatibleProvider`, multi-stage non-root images, Caddy, health endpoints, `.env.example`s | `docker compose -f compose.prod.yml up` locally |
| D.2 | `docs/DEPLOYMENT.md`, then the actual deploy **after your go-ahead** | live URL |

README updates (architecture Mermaid diagram, threat model, limitations) happen at the end of Phase 4 and
again after deploy. All metrics stay `TODO: fill after running redteam/eval` until real runs produce them.

## 4. What I'll need from you (only when we get there)
- **Before D.2:** a hosted LLM key (Groq is simplest), a Neon connection string, a Qdrant Cloud URL + key,
  the VM, and a domain decision. All go in `.env` files that are never committed.
- Phase 3 later: Razorpay **test** key ID/secret and a webhook secret.

## 5. Decisions confirmed (2026-10-25)
- Postgres + Alembic replaces SQLite; fresh `chunks_v2` Qdrant collection, no data migration.
- `/audio/transcribe` and `/images/analyze` are **removed**; upload covers both modalities.
- Deploy uses an owned domain: `app.<domain>` (Vercel) + `api.<domain>` (VM), same-site cookies.
- Worker: **arq**, with untrusted parsing in a killable subprocess.

## 6. Existing local data
Old Qdrant points and the SQLite registry have no owner. The new code uses a fresh `chunks_v2` collection
and Postgres; the sample files in `examples/` are re-ingested into the demo workspace. Nothing is migrated.
