FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app.py .
COPY templates/ templates/

FROM python:3.13-slim

LABEL org.opencontainers.image.title="tmnf-leaderboard"
LABEL org.opencontainers.image.description="Live leaderboard for TrackMania Nations Forever LAN parties with fanfare on new records"
LABEL org.opencontainers.image.url="https://github.com/madsthom/tmnf-leaderboard"
LABEL org.opencontainers.image.source="https://github.com/madsthom/tmnf-leaderboard"

WORKDIR /app
COPY --from=builder /app /app

EXPOSE 8080

CMD ["/app/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
