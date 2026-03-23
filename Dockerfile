# ── Build stage ───────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="Orion Consultant"
LABEL description="Expert Committee for Step Index trading decisions"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY . .

# Non-root user for security
RUN useradd --create-home orion
USER orion

# Expose FastAPI port
EXPOSE 8100

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:8100/health')"

# Default: run the FastAPI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
