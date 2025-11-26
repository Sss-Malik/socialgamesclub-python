import threading
import requests
import json
from settings import WEBHOOK_URL, WEBHOOK_SECRET

def notify_webhook_async(results: dict, request_type: str):
    """Send webhook data in a background thread."""

    def _send():
        results["request_type"] = request_type
        payload = {"results": results}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {WEBHOOK_SECRET}"
        }

        try:
            response = requests.post(WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=60)
            response.raise_for_status()
            print(f"✅ Webhook sent ({response.status_code})")
        except requests.exceptions.RequestException as e:
            print("❌ Webhook failed:", e)

    # Start the thread (non-blocking)
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


from datetime import datetime, date

def serialize_model(obj):
    """Convert SQLAlchemy model object into a JSON-serializable dict."""
    if obj is None:
        return None

    # handle list of objects
    if isinstance(obj, list):
        return [serialize_model(o) for o in obj]

    # handle SQLAlchemy model instances
    if hasattr(obj, "__table__"):
        data = {}
        for column in obj.__table__.columns:
            value = getattr(obj, column.name)
            data[column.name] = serialize_value(value)
        return data

    # fallback for primitives or unexpected objects
    return serialize_value(obj)


def serialize_value(value):
    """Convert non-serializable types into JSON-safe formats."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)  # fallback for anything else
