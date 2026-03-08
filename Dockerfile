# ─── Stage 1: Build dependencies ────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt


# ─── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.11-slim AS runtime

ARG VERSION=dev
ARG BUILD_DATE=unknown
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="AutoRCA API"
LABEL org.opencontainers.image.description="Automated Root Cause Analysis engine"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${VCS_REF}"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Non-root user for security
RUN groupadd -r autorca && useradd -r -g autorca autorca

# Copy installed packages from builder
COPY --from=builder /root/.local /home/autorca/.local

# Copy application code
COPY api_server.py ai_analyzer.py ./
COPY --chown=autorca:autorca . .

# Ensure scripts in .local are available
ENV PATH=/home/autorca/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000

# Drop to non-root
USER autorca

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT}/api/health \
    -H "X-API-Key: ${AUTORCA_API_KEY}" || exit 1

EXPOSE ${PORT}

CMD ["python", "api_server.py"]