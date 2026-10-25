# Multimodal RAG Research Assistant

A full-stack Retrieval-Augmented Generation (RAG) system. Upload PDFs, images, and audio,
then ask natural-language questions and get answers grounded in your own documents with
source citations (page numbers, audio timestamps, or chunk IDs).

Real retrieval, embedding, reranking, and generation pipeline — dense + BM25 hybrid
search, cross-encoder reranking, OCR for scanned documents, Whisper for audio, and a
swappable local/hosted LLM backend.

## Features

- **Multimodal ingestion** — PDFs (native text or OCR fallback for scanned pages),
  images (OCR + vision captioning), and audio (Whisper transcription with timestamps)
- **Hybrid retrieval** — dense vector search (Qdrant) + BM25 keyword search, combined via
  Reciprocal Rank Fusion, toggleable independently
- **Reranking** — optional cross-encoder reranking stage (BAAI/bge-reranker-base) for
  higher-precision results
- **Grounded generation** — answers cite sources by document/page/timestamp, abstain when
  context is insufficient, and never fabricate page numbers
- **Configurable pipeline** — chunk size/overlap, retrieval top-k, final context chunk
  count, similarity threshold, temperature, top-p, and max tokens are all adjustable from
  the UI's Advanced Retrieval Settings panel
- **Swappable LLM provider** — defaults to local Ollama (free, no API key); swapping to
  another provider (Groq, OpenAI, Anthropic, ...) is a config change + one new provider
  class, no other code changes
- **Structured error handling** — invalid file types, empty documents, OCR/transcription
  failures, missing API keys, no retrieval results, etc. all return clear structured errors
- **Evaluation harness** — a real retrieval-quality eval script (Recall@K, Precision@K,
  MRR) against a live Qdrant instance

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React, Vite, Tailwind CSS, Axios, react-markdown, lucide-react |
| Backend | Python, FastAPI, Pydantic v2, Uvicorn |
| PDF extraction | PyMuPDF |
| OCR | PaddleOCR |
| Speech-to-text | faster-whisper |
| Embeddings | sentence-transformers, BAAI/bge-small-en-v1.5 |
| Reranking | sentence-transformers CrossEncoder, BAAI/bge-reranker-base |
| Sparse retrieval | rank_bm25 |
| Vector DB | Qdrant |
| LLM generation & vision | Ollama (local, default: `llama3.2:3b` + `moondream`) |
| Document registry | PostgreSQL (SQLAlchemy + Alembic) |
| Infra | Docker, Docker Compose |

## Project structure

```
RAG/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app + startup model loading
│   │   ├── config.py          # all tunables (env-driven)
│   │   ├── routers/           # documents, chat, search, audio, images
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── services/          # chunking, embedding, retrieval, reranking,
│   │   │   └── llm/           # OCR, whisper, vision, LLM provider abstraction
│   │   ├── models/, core/, storage/
│   ├── data/uploads/          # saved files (gitignored)
│   ├── eval/                  # eval_dataset.json + run_eval.py
│   ├── tests/                 # pytest suite
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.jsx            # Sidebar | ChatArea | SettingsPanel layout
│   │   ├── api/client.js      # Axios wrapper for every endpoint
│   │   ├── components/        # layout, documents, chat, common
│   │   ├── context/, hooks/, utils/
│   └── .env.example
├── examples/                  # sample PDF / image / audio for a test run
├── docker-compose.yml         # qdrant + backend
└── README.md
```

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker Desktop
- [Ollama](https://ollama.com) installed and running

## Setup

### 1. Environment variables

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Every variable is documented inline in `backend/.env.example`. No API key is needed with
the default Ollama setup.

### 2. Pull the Ollama models

```bash
ollama pull llama3.2:3b
ollama pull moondream
```

(Sized to run on a 4GB-VRAM GPU. Swap in larger models — e.g. `llama3.1:8b`, `llava` — in
`.env` if you have more VRAM to spare.)

### 3. Start Postgres, Redis and Qdrant

```bash
docker compose up -d postgres redis qdrant
```

The backend creates the `chunks_v2` collection automatically on first startup — no
manual setup needed. Qdrant's dashboard is browsable at `http://localhost:6333/dashboard`.
Postgres also gets a separate `sentinel_test` database for the test suite.

### 4. Run the backend

```bash
cd backend
python -m venv venv
./venv/Scripts/activate        # Windows; use `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
alembic upgrade head           # create / migrate the Postgres schema
python -m app.cli seed-demo    # one-time: load examples/ into the demo template
uvicorn app.main:app --reload --port 8000
```

First startup downloads the embedding/reranker models from Hugging Face (a few hundred MB,
one-time, cached afterward). Startup takes ~20–30 s while models load; the API answers once
the log says `Application startup complete`.

Optional:

- `python -m app.cli create-admin --email you@example.com` creates an admin account
  (prompts for a password). There is no way to become admin through the web app.
- Set `JWT_SECRET_KEY` in `backend/.env` to stay signed in across backend restarts. Without
  it, a random key is used and everyone is signed out whenever the backend restarts.

### 5. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open **`http://localhost:5173`** (use `localhost`, not `127.0.0.1`: CORS and the session
cookies are configured for `localhost`).

### Day-to-day

After the first setup, four terminals:

```bash
docker compose up -d postgres redis qdrant      # repo root; also make sure Ollama is running
cd backend && ./venv/Scripts/activate && uvicorn app.main:app --reload --port 8000
cd backend && ./venv/Scripts/activate && arq app.worker.WorkerSettings     # processes uploads
cd frontend && npm run dev
```

Uploads show as *queued* until the worker processes them. Without the worker running they
stay queued; set `INGEST_MODE=inline` in `backend/.env` to process uploads inside the API
instead (fine for a quick try, but the API then loads OCR/Whisper itself).

Run `alembic upgrade head` again after pulling changes that add migrations.

### Or run backend + Qdrant via Docker Compose

```bash
docker compose up -d --build
```

The backend container reaches your host's Ollama via `host.docker.internal:11434`
(already configured in `docker-compose.yml`). Run the frontend separately with
`npm run dev`.

## Try it

1. On the sign-in page, click **Try the demo, no sign-up**. You get a private, 24-hour
   workspace that already contains the sample files from `examples/` (a PDF, an image, and
   an audio clip), with 20 queries per day. Or create an account and upload them yourself.
2. Ask a question that spans multiple documents (e.g. *"What mechanism does the
   transformer architecture rely on?"*) — the answer will cite each source with its page
   number, audio timestamp, or chunk ID. The first answer is slower while Ollama loads the
   model.
3. Open **Advanced Retrieval Settings** and toggle Reranking / Hybrid Search, or adjust
   Top-K and Temperature, to see retrieval and answers change.

## API reference

| Method | Path | Purpose |
|---|---|---|
| POST | `/documents/upload` | Upload + ingest a PDF/image/audio file (`?force_ocr=true` to force OCR on a PDF) |
| GET | `/documents` | List all documents with status/chunk counts |
| DELETE | `/documents/{document_id}` | Delete a document and its chunks |
| GET | `/documents/{document_id}/chunks` | Inspect a document's stored chunks |
| POST | `/documents/reset` | Wipe all documents, vectors, and the BM25 index |
| POST | `/chat` | Ask a question; returns a grounded answer + citations + retrieval metadata |
| POST | `/search` | Retrieval only, no generation |
| POST | `/audio/transcribe` | Standalone Whisper transcription |
| POST | `/images/analyze` | Standalone OCR + vision description |
| GET | `/health` | Liveness check |

Example `/chat` response:

```json
{
  "answer": "...",
  "sources": [
    { "index": 1, "document": "transformers.pdf", "file_type": "pdf", "page": 14,
      "chunk_id": "...", "score": 0.87, "text_snippet": "..." }
  ],
  "retrieval": { "top_k": 10, "reranked": true, "hybrid": true, "reranked_chunks": 4,
                 "dense_candidate_count": 10, "bm25_candidate_count": 10 }
}
```

## Testing

```bash
cd backend
pytest tests/ -v          # requires Qdrant running for most tests (auto-skipped otherwise)
python eval/run_eval.py   # retrieval quality metrics (Recall@K, Precision@K, MRR)
```

## Known limitations

- Default local models (`llama3.2:3b`, `moondream`) are small, chosen to fit 4GB VRAM —
  they can misread numbers or produce shallower answers than larger/hosted models.
- BM25 index is rebuilt in-memory on every document add/delete (fine at small scale, not
  incrementally optimized).
- Scanned-page detection is heuristic; use `?force_ocr=true` to override it.
- File upload is processed synchronously (no background job queue).
- Frontend isn't containerized (runs via `npm run dev` for fast local iteration).
