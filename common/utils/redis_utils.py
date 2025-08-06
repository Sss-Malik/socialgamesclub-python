import redis
from settings import CELERY_BROKER_URL
redis_client = redis.StrictRedis.from_url(CELERY_BROKER_URL)

def acquire_login_lock(backend_name: str, timeout=60):
    key = f"login-lock:{backend_name}"
    return redis_client.set(key, "locked", nx=True, ex=timeout)

def release_login_lock(backend_name: str):
    key = f"login-lock:{backend_name}"
    redis_client.delete(key)