"""ローカル専用アクセスの防御。

パスワード無しの運用なので、次の 2 点で「同じ PC のブラウザで開いた別サイト」からの
不正操作を防ぐ。
1. Host ヘッダーが 127.0.0.1 / localhost 以外なら拒否（DNS リバインディング対策）。
2. 状態を変える API は独自ヘッダー X-Teacher-Agent を必須にする。別オリジンのページが
   このヘッダーを付けるには CORS プリフライトが必要だが、本サーバーは CORS を一切
   許可しないため送れない。Origin が付いていればローカル以外は拒否する。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-teacher-agent"


def _hostname(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if _hostname(host) not in LOCAL_HOSTS:
            return JSONResponse({"detail": "ローカル以外からのアクセスは許可されていません"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and _hostname(urlsplit(origin).netloc) not in LOCAL_HOSTS:
                return JSONResponse({"detail": "許可されていないオリジンです"}, status_code=403)
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "必要なヘッダーがありません"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'")
        return response
