#!/bin/sh
set -eu
if [ "${STOCKS_IMPORT_MODE:-0}" = "1" ]; then
    exec python scripts/railway-import-server.py
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8787}" --workers 1 --proxy-headers
