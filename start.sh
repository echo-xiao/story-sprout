#!/bin/sh
# Start FastAPI backend on port 8000 (internal)
uvicorn src.app:app --host 0.0.0.0 --port 8000 &

# Start Next.js frontend on PORT (Cloud Run's port, default 8080)
cd frontend
API_URL=http://localhost:8000 PORT=${PORT:-8080} HOSTNAME=0.0.0.0 node server.js
