FROM ghcr.io/astral-sh/uv:0.11.26 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY web ./web
COPY scripts/start-railway.sh ./scripts/start-railway.sh
COPY scripts/railway-import-server.py ./scripts/railway-import-server.py
COPY scripts/accounts.py ./scripts/accounts.py
COPY scripts/backup_sqlite.py ./scripts/backup_sqlite.py
CMD ["sh", "scripts/start-railway.sh"]
