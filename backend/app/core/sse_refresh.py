"""
SSE stream for refresh-done notifications.
Worker publishes to Valkey channel refresh:{service_type}:{region}; this module
subscribes in a background thread and yields one SSE event when the message is received.
"""
import queue
import threading
from app.core.valkey_client import get_client
from app.core.config import settings


def stream_refresh_done(channel: str, timeout_seconds: int | None = None):
    """
    Yield SSE bytes until a message is received on the given Valkey channel, or timeout.
    channel should be "refresh:{service_type}:{region}".
    """
    if timeout_seconds is None:
        timeout_seconds = settings.refresh_stream_timeout_seconds
    msg_queue = queue.Queue()

    def listen():
        try:
            pubsub = get_client().pubsub()
            pubsub.subscribe(channel)
            for msg in pubsub.listen():
                if msg.get("type") == "message":
                    msg_queue.put("done")
                    break
        except Exception:
            msg_queue.put("done")  # On error, unblock the client
        finally:
            try:
                pubsub.unsubscribe(channel)
                pubsub.close()
            except Exception:
                pass

    t = threading.Thread(target=listen, daemon=True)
    t.start()

    deadline_ticks = timeout_seconds
    while deadline_ticks > 0:
        try:
            msg_queue.get(timeout=1)
            yield "event: refresh_done\ndata: {}\n\n"
            return
        except queue.Empty:
            deadline_ticks -= 1
            yield ": keepalive\n\n"
    yield "event: refresh_done\ndata: {\"timeout\": true}\n\n"
