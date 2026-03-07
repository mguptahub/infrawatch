import hashlib
import hmac
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import settings

MAX_DRIFT_SECONDS = 30
BYPASS_PATHS = {"/", "/api/health"}


class HMACMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        secret = self._get_secret()

        if not secret:
            return await call_next(request)

        if request.url.path in BYPASS_PATHS:
            return await call_next(request)

        hmac_header = request.headers.get("x-infrawatch-hmac", "")
        timestamp = request.headers.get("x-infrawatch-timestamp", "")

        if not hmac_header or not timestamp:
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": "Missing HMAC headers"},
            )

        try:
            ts = int(timestamp)
        except ValueError:
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": "Invalid timestamp"},
            )

        drift = abs(time.time() - ts)
        if drift > MAX_DRIFT_SECONDS:
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": "Request expired"},
            )

        expected = hmac.new(
            secret.encode(),
            (timestamp + request.url.path).encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(hmac_header, expected):
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": "Invalid HMAC"},
            )

        return await call_next(request)

    def _get_secret(self) -> str:
        if settings.hmac_secret_file:
            try:
                with open(settings.hmac_secret_file, "r") as f:
                    return f.read().strip()
            except FileNotFoundError:
                pass
        return settings.infrawatch_hmac_secret
