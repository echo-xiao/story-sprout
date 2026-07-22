"""Single shared-passcode gate — the app's ONLY auth.

A POST to a generation endpoint must carry an `x-access-code` header matching
`config.ACCESS_CODE`, else 403. Reads (GET/HEAD) and non-generation POSTs pass
through. An empty ACCESS_CODE disables the gate.

Lives in its own module (no heavy router imports) so it stays unit-testable and
so app.py just wires it in.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# POST paths ending in one of these are "generation" and require the passcode.
GEN_SUFFIXES = (
    "/generate", "/regenerate", "/simplify", "/background",
    "/summarize", "/autofill", "/quality", "/consistency",
)


class AccessCodeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        from src.config import ACCESS_CODE
        path = request.url.path
        is_gen = request.method == "POST" and any(path.endswith(s) for s in GEN_SUFFIXES)
        if is_gen and ACCESS_CODE and request.headers.get("x-access-code") != ACCESS_CODE:
            return JSONResponse(
                {"detail": "访问口令不正确 — 需要有效口令才能生成。"},
                status_code=403,
            )
        return await call_next(request)
