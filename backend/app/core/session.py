import json
import secrets
from .valkey_client import get_client

SESSION_TIMEOUT_SECONDS = 15 * 60
SESSION_TIMEOUT_MINUTES = 15  # kept for cookie max_age calculations
SESSION_COOKIE_NAME = "session_id"


class SessionStore:
    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def create_session(self, config: dict) -> str:
        session_id = secrets.token_urlsafe(32)
        get_client().setex(self._key(session_id), SESSION_TIMEOUT_SECONDS, json.dumps(config))
        return session_id

    def update_session(self, session_id: str, config: dict = None):
        key = self._key(session_id)
        client = get_client()
        if config:
            client.setex(key, SESSION_TIMEOUT_SECONDS, json.dumps(config))
        else:
            client.expire(key, SESSION_TIMEOUT_SECONDS)  # slide TTL only

    def get_session_config(self, session_id: str):
        data = get_client().get(self._key(session_id))
        if not data:
            return None
        return json.loads(data)

    def delete_session(self, session_id: str):
        get_client().delete(self._key(session_id))


session_store = SessionStore()
