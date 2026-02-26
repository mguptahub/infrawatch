from fastapi import Request
from slowapi import Limiter
from .config import settings


def get_real_ip(request: Request) -> str:
    """Use the real client IP from forwarded headers set by nginx, not the proxy IP."""
    real_ip = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "")
    if real_ip:
        return real_ip.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=get_real_ip, storage_uri=settings.valkey_url)
