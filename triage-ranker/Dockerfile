# triage-ranker/Dockerfile
# Multi-stage build: spaCy model downloaded in builder, not at runtime.
# This avoids the pitfall of downloading the model at container startup
# (Kenya's network cannot be assumed during a live call).

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Download spaCy model in builder stage — not at runtime
RUN python -m spacy download en_core_web_md

COPY app/ app/

FROM python:3.11-slim AS runtime

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app

# Copy spaCy model from builder's venv to a stable location
RUN mkdir -p /app/models
COPY --from=builder /app/.venv/lib/python3.11/site-packages/en_core_web_md /app/models/en_core_web_md

ENV PATH="/app/.venv/bin:$PATH"
ENV SPACY_MODEL_PATH=/app/models/en_core_web_md

# Create cache directory for SQLite L2 cache
RUN mkdir -p /app/cache

EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
