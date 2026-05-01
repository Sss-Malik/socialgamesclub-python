import functools
from playwright.sync_api import Page
from common.utils.playwright_pool import BROWSER, PAGE_SEM  # see below
from common.utils.stealth import apply_stealth_sync
import threading

_persistent_contexts: dict[str, any] = {}
_contexts_lock = threading.Lock()

# Backends that need full anti-fingerprint stealth (Cloudflare/bot detection).
# Keep this set as small as possible — every other backend uses the legacy
# context profile that has been stable in production.
STEALTH_BACKENDS = {"river"}

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/115.0.0.0 Safari/537.36"
)
# Sourced from pw-stealth-enhanced DEFAULT_USER_AGENT_POOL[0].
# Public so backend automations can pass a matching UA to anticaptcha
# for reCAPTCHA v3 fingerprint consistency.
STEALTH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def get_or_create_context(backend: str):
    with _contexts_lock:
        if backend in _persistent_contexts:
            return _persistent_contexts[backend]

        is_stealth = backend in STEALTH_BACKENDS

        # Only one thread can reach here per backend
        context = BROWSER.new_context(
            user_agent=STEALTH_UA if is_stealth else _DEFAULT_UA,
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            color_scheme="light",
        )
        print(f"[INFO] Created new context for backend={backend} (stealth={is_stealth})")
        if is_stealth:
            apply_stealth_sync(context, locale="en-US", timezone_id="America/New_York")
        else:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
        _persistent_contexts[backend] = context
        return context

def with_persistent_browser(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        backend = kwargs.get("backend")
        if not backend:
            raise ValueError("with_persistent_browser requires a 'backend' keyword argument")

        context = get_or_create_context(backend)

        PAGE_SEM.acquire()
        page = None
        try:
            page = context.new_page()
            return fn(page=page, *args, **kwargs)
        except Exception as e:
            print(f"[ERROR] Playwright function error for backend={backend}: {e}")
            raise
        finally:
            try:
                if page:
                    page.close()
            except Exception as close_err:
                # Log warning instead of crashing the app
                print(f"[WARN] Failed to close Playwright page: {close_err}")
            PAGE_SEM.release()

    return wrapper

