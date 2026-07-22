# Vercel Python serverless entry — exposes the FastAPI ASGI app. Vercel routes
# /api/* here (see vercel.json); each request is short (per Plan 3's per-page model).
from src.app import app  # noqa: F401  (Vercel's ASGI adapter imports `app`)
