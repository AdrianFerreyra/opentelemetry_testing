# ── Builder stage: install dependencies with uv ──────────────────────────────
FROM python:3.12-slim AS builder

# Copy uv binary from the official image (no install script needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first — maximises Docker layer cache reuse
COPY pyproject.toml uv.lock ./

# Create a dummy app package so hatchling can build the project
RUN mkdir -p app && touch app/__init__.py

# Install production deps into .venv
# --frozen: fail if uv.lock is out of sync with pyproject.toml
# --no-dev:  exclude dev dependencies
# --no-editable: install as a regular (non-editable) package
RUN uv sync --frozen --no-dev --no-editable

# ── Final stage: lean runtime image ──────────────────────────────────────────
FROM python:3.12-slim AS final

WORKDIR /app

# Copy the pre-built virtualenv from the builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY app/ ./app/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
