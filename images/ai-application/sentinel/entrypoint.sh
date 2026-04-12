#!/bin/sh
# Start FastAPI backend then nginx in foreground
uvicorn main:app --host 127.0.0.1 --port 8080 --workers 2 &
nginx -g "daemon off;"
