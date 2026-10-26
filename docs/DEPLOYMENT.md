# Deploying Sentinel RAG

```
            app.<domain>                         api.<domain>
 Browser ──────────────► Vercel (React)          VM (Docker Compose)
    │                                            ┌──────────────────────────────────┐
    └──── HTTPS, cookies, CSRF ────────────────► │ Caddy :443 (Let's Encrypt)       │
                                                 │   └─► api (FastAPI)              │
                                                 │ worker (arq) ◄─► redis           │
                                                 │ volumes: uploads, model caches   │
                                                 └───────┬───────────────┬──────────┘
                                                         │               │
                                              Neon (Postgres)   Qdrant Cloud   Hosted LLM (Groq/Gemini/NIM)
```

The frontend and API live on two subdomains of one domain (`app.` and `api.`), so they are
*same-site*: the API's `SameSite=Lax` httpOnly cookies work without third-party-cookie
exceptions, and CORS allows exactly one origin.

## What you need

| Account | Used for | Free option |
|---|---|---|
| A domain | `app.` and `api.` subdomains, TLS certificates | Namecheap `.me` via the GitHub Student Pack |
| A Linux VM, 4 vCPU / 8 GB RAM or more, 40 GB disk | API, worker, Redis, Caddy | Oracle Cloud Always Free (Ampere ARM), or DigitalOcean/Azure credits from the Student Pack |
| Neon | Postgres | Free tier |
| Qdrant Cloud | Vector database | Free 1 GB cluster |
| An OpenAI-compatible LLM API key | Answer generation (and optionally image captions) | Groq free tier; or Google Gemini, NVIDIA NIM |
| Vercel | Frontend hosting | Hobby plan |

Redis runs on the VM rather than Upstash: the queue worker polls Redis continuously, which
would exhaust Upstash's free command quota. (`REDIS_URL` can point at Upstash if you prefer.)

**Memory.** The API loads the embedding model, reranker and injection classifier; the worker
loads the embedding model, classifier, OCR and Whisper on first use. Plan for ~3 GB per
process at peak. 8 GB total is comfortable; 4 GB works only with `SECURITY_CLASSIFIER_ENABLED=false`
and `WHISPER_MODEL_SIZE=base`.

**ARM VMs (Oracle Ampere).** PaddlePaddle's Linux ARM wheels are not guaranteed. If the image
build fails on `paddlepaddle`, use an x86 VM, or switch OCR to RapidOCR (same PaddleOCR models
via ONNX Runtime). That switch isn't implemented yet.

## Before you deploy: checklist

- [ ] Backend tests pass, including the tenant-isolation suite: `cd backend && pytest`
- [ ] Red-team gate passes: `python redteam/run_eval.py --classifier off --max-asr 0.28 --max-fpr 0.02`
- [ ] `deploy/.env.prod` filled in, with a fresh `JWT_SECRET_KEY`; `git status` shows it untracked
- [ ] Payments: `RAZORPAY_KEY_ID` starts with `rzp_test_`, `PAYMENTS_TEST_MODE=true`, and the
      webhook secret is set (the API refuses to start otherwise); `pytest tests/test_billing.py`
      passes (webhook signature verification and idempotency)

## 1. Managed services

**Neon.** Create a project. Copy the connection string and change the scheme to the psycopg
driver, keeping TLS on:

```
postgresql+psycopg://USER:PASSWORD@ep-xxxx.REGION.aws.neon.tech/neondb?sslmode=require
```

**Qdrant Cloud.** Create a free cluster and an API key. You need the cluster URL (ending in
`:6333`) and the key. The app creates the `chunks_v2` collection and its tenant index on
first start.

**LLM.** Create an API key. Base URLs:

| Provider | `LLM_BASE_URL` | Example `LLM_MODEL` |
|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` (with `LLM_REASONING_EFFORT=low`) |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.0-flash` |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `meta/llama-3.3-70b-instruct` |

## 2. DNS

- `api.<domain>`: an **A record** pointing to the VM's public IP.
- `app.<domain>`: a **CNAME** to `cname.vercel-dns.com` (Vercel shows the exact value).

## 3. The VM

```bash
# Ubuntu 22.04/24.04
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

# Firewall: SSH and web only. (On Oracle Cloud, also open 80/443 in the VCN security list.)
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp && sudo ufw enable

git clone https://github.com/vigildas05/Multimodel-RAG.git sentinel-rag
cd sentinel-rag/deploy
cp .env.prod.example .env.prod && chmod 600 .env.prod
nano .env.prod          # fill in every value (see comments in the file)

docker compose --env-file .env.prod -f compose.prod.yml up -d --build
docker compose --env-file .env.prod -f compose.prod.yml ps
curl https://api.<domain>/health/ready     # {"status":"ready", ...} once models have loaded
```

The first start downloads models (~2 GB) into the `model_cache` volume; later restarts reuse it.
`migrate` runs `alembic upgrade head` and exits; the API and worker start after it succeeds.

One-time setup:

```bash
C="docker compose --env-file .env.prod -f compose.prod.yml"
$C run --rm -v "$PWD/../examples:/examples:ro" api python -m app.cli seed-demo --samples-dir /examples
$C run --rm api python -m app.cli create-admin --email you@example.com
```

## 3b. Razorpay (test mode, optional)

Leave the three `RAZORPAY_*` variables empty to run without payments (the billing page says so).
To enable test payments:

1. In the Razorpay dashboard, switch to **Test Mode** and generate API keys
   (`rzp_test_...` key ID + secret). Live keys are refused at startup.
2. **Settings → Webhooks → Add**: URL `https://api.<domain>/webhooks/razorpay`, a strong secret,
   events `payment.captured`, `order.paid`, `payment.failed`.
3. Put the key ID, key secret and webhook secret in `deploy/.env.prod`, then
   `docker compose --env-file .env.prod -f compose.prod.yml up -d`.
4. Buy a pack on the Billing page: in Razorpay Checkout choose **Netbanking**, any bank, then
   **Success** (or domestic test card `5267 3181 8797 5449`, or UPI `success@razorpay`).
   `4111 1111 1111 1111` is an international card and is declined unless the Razorpay account
   accepts international payments. Credits appear when the webhook arrives (usually within seconds).

Credits are granted only from the signed webhook, never from the browser's checkout callback.

## 4. Vercel (frontend)

1. Import the GitHub repository; set **Root Directory** to `frontend` (Vite is detected).
2. Environment variable: `VITE_API_BASE_URL=https://api.<domain>`.
3. In `frontend/vercel.json`, set `connect-src` to your API
   origin, so the Content-Security-Policy allows the API calls. Commit and push.
4. Add the custom domain `app.<domain>`.

## 5. Verify

- `https://api.<domain>/health/ready` returns `ready`, and `/docs` returns 404 (disabled in production).
- `curl -I https://api.<domain>/health` shows `strict-transport-security` and `content-security-policy`.
- `https://app.<domain>`: "Try the demo", ask a question, upload a PDF (it goes *queued* → *ready*).
- From another origin, an API call is refused by CORS.

## Operating it

```bash
C="docker compose --env-file .env.prod -f compose.prod.yml"
$C logs -f api worker              # JSON access logs from Caddy; app logs carry request IDs
git pull && $C up -d --build       # deploy an update (migrations run first)
$C restart worker
```

- **Backups.** Neon keeps point-in-time history. Qdrant Cloud snapshots can be taken from its
  console. Uploads are deleted after processing, so the VM holds no user files at rest.
- **Secrets.** Only in `deploy/.env.prod` (mode 600, never committed). Rotating `JWT_SECRET_KEY`
  signs everyone out.
- **Demo cleanup** runs every 15 minutes in the worker.
- **Logs** are JSON (`LOG_FORMAT=json`), one object per line with a `request_id` you can match
  to the `X-Request-ID` response header: `$C logs api | jq 'select(.level=="ERROR")'`.
- **Metrics.** Set `METRICS_TOKEN` to enable `/metrics`. Caddy refuses it publicly; run a
  Prometheus container on the same compose network scraping `http://api:8000/metrics` with
  `authorization: { credentials: <token> }`.
- **Admin dashboard** at `https://app.<domain>/admin` (admins only). The red-team tab reads
  the reports committed in `redteam/reports/`, mounted read-only into the API container;
  after re-running the eval, commit the new report and `git pull` on the VM.

## Hardening already in place

- Containers run as a non-root user (uid 10001) with read-only root filesystems, all Linux
  capabilities dropped and `no-new-privileges`; only Caddy publishes ports.
- The API refuses to start in production with a wildcard or non-HTTPS `CORS_ORIGINS`, insecure
  cookies or a missing/weak `JWT_SECRET_KEY`.
- HSTS, CSP and other security headers on API responses; the frontend's headers come from
  `frontend/vercel.json`.
- The API trusts `X-Forwarded-For` only on the private Docker network, where Caddy is the only
  peer, so rate limits see real client IPs.

## Verifying the production stack locally

`deploy/compose.smoke.yml` runs the same stack against local Postgres/Qdrant containers and the
host's Ollama, with Caddy serving `https://localhost` from its local CA:

```bash
cd deploy
# .env.smoke: like .env.prod, plus ENV_FILE=.env.smoke, API_DOMAIN=localhost and local URLs
docker compose --env-file .env.smoke -f compose.prod.yml -f compose.smoke.yml up -d --build
curl -k https://localhost/health/ready
```
