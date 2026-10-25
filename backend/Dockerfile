# syntax=docker/dockerfile:1
# One image for both the API and the background worker (different commands).

# ---- build stage: compile dependencies into a self-contained virtualenv --------------------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# CPU-only PyTorch: the default CUDA build adds several GB, and these servers have no GPU.
ARG TORCH_VERSION=2.14.0
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}"

COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- runtime stage: slim image, no compilers, non-root user ---------------------------------
FROM python:3.11-slim AS runtime

# libgl1/libglib2.0-0/libgomp1: OpenCV and PaddleOCR runtime libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
    HF_HOME=/home/app/.cache/huggingface

WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app security ./security
COPY --chown=app:app app ./app
# Pre-create every writable mount point owned by the app user: Docker seeds a new named
# volume with the image directory's ownership, so these stay writable for uid 10001.
RUN mkdir -p /app/data/uploads /home/app/.cache/huggingface /home/app/.paddleocr \
    && chown -R app:app /app/data /home/app/.cache /home/app/.paddleocr

USER app
EXPOSE 8000

# Liveness for the API. (The worker overrides this in compose with `arq --check`.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=240s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# --proxy-headers: trust X-Forwarded-For from the reverse proxy (see FORWARDED_ALLOW_IPS in
# deploy/compose.prod.yml) so rate limits see real client IPs. One process: models are large.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
