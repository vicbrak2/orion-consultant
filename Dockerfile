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

# Install system dependencies for Chromium (must run as root before USER orion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright chromium browser binary
RUN pip install playwright>=1.40.0 && playwright install chromium

# Non-root user for security
RUN useradd --create-home orion
RUN mkdir -p /app/snapshots && chown -R orion:orion /app/snapshots
USER orion

# Expose FastAPI port
EXPOSE 8090

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:8090/health')"

# Default: run the FastAPI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
