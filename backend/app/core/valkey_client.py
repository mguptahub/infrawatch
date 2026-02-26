import json
import hashlib
import os
from valkey import Valkey

VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379")
CACHE_TTL = 900  # 15 minutes default

_client: Valkey = None


def get_client() -> Valkey:
    global _client
    if _client is None:
        _client = Valkey.from_url(VALKEY_URL, decode_responses=True)
    return _client


def make_cache_key(resource: str, access_key: str, region: str) -> str:
    """Build a cache key scoped to the AWS account and region."""
    key_hash = hashlib.sha256((access_key or "").encode()).hexdigest()[:16]
    return f"cache:{resource}:{key_hash}:{region}"


def get_cached(key: str, ttl: int, fetcher, force: bool = False):
    """
    Return cached data from Valkey, or call fetcher() and cache the result.
    If force=True, bypass the cache and refresh the stored value.
    Falls back to fetcher() if Valkey is unavailable.
    """
    client = get_client()

    if not force:
        try:
            hit = client.get(key)
            if hit:
                return json.loads(hit)
        except Exception:
            pass  # Cache unavailable — proceed to live fetch

    data = fetcher()

    try:
        client.setex(key, ttl, json.dumps(data, default=str))
    except Exception:
        pass  # Cache write failed — return data anyway

    return data
