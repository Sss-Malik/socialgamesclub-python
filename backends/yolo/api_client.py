"""
YOLO777 agent backend HTTP client.

A pure-HTTP implementation (no browser) of the Laravel 7 + Dcat Admin
agent portal at https://agent.yolo-777.com. The full reverse-engineering
spec this implements lives at:

    /Applications/development/yolo-standalone/yolo_api.md

Key facts driving the design (some corrected from live recon):
  - Auth is a Laravel session cookie. The deployed cookie name is
    `yolo_session` (the spec's `laravel_session` is the framework default;
    this install overrides SESSION_COOKIE). We persist the whole cookie
    jar in backend_sessions so a session survives across workers/process
    restarts, and only re-login on the documented "session dead" symptoms.
  - Writes also need a CSRF token (`Dcat.token`, a stable per-session
    40-char value). It is re-fetchable from any admin page, so it is NOT
    persisted — it is fetched lazily and cached on the instance.
  - There are three response envelopes (spec §7):
      * action success/business-error : HTTP 200 + {"status":bool,"data":{...}}
      * create success                : HTTP 200 + {"status":true,"data":{"message":"<html>",...}}
      * validation error              : HTTP 422 + {"status":false,"errors":{...}}
    Always branch on the JSON `status` boolean.
"""

import json
import logging
import re
import urllib.parse
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.utils.db_actions import (
    create_backend_session,
    get_latest_valid_session,
    invalidate_latest_session,
)
from common.utils.redis_utils import acquire_login_lock, release_login_lock
from common.utils.poll_utils import wait_for_valid_session


class YoloAPIError(Exception):
    def __init__(self, message: str):
        super().__init__(f"yolo api error: {message}")
        self.message = message


# Dcat action class identifiers (spec §4, §5).
_RECHARGE_FORM = r"App\Admin\Actions\UserRecharge"
_RESET_FORM = r"App\Admin\Actions\ResetUserPass"

# type=1 recharge (credit player / debit agent); type=2 redeem (debit player /
# credit agent). (spec §4)
TYPE_RECHARGE = 1
TYPE_REDEEM = 2

# Grid column indices, confirmed live against the player_list <thead>:
#   0 Player ID | 1 Account | 2 nickname | 3 AgentAccount | 4 KindName |
#   5 Player Score | 6 Total recharge | ... | 15 Action
_COL_PLAYER_ID = 0
_COL_ACCOUNT = 1
_COL_PLAYER_SCORE = 5


class YoloClient:
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        backend_name: str,
        register_ip: str = "0.0.0.0",
        logger: Optional[logging.Logger] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not base_url or not username or not password:
            raise ValueError("YoloClient requires base_url, username, password.")

        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.backend_name = backend_name
        self.register_ip = register_ip
        self.timeout = timeout
        self.logger = logger or logging.getLogger("casino_automation.yolo.api")

        self.session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
            status_forcelist=(502, 503, 504),
            # Only auto-retry idempotent GETs. Retrying a mutating POST
            # (recharge/withdraw) on a read timeout risks a double-apply if the
            # first request reached the server before the response timed out.
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        })

        self._authenticated = False
        self._csrf_token: Optional[str] = None
        self._db_session_id: Optional[int] = None

    # -- session id used by callers for active_tasks_count bookkeeping -----

    @property
    def db_session_id(self) -> Optional[int]:
        return self._db_session_id

    # -- cookie persistence ------------------------------------------------

    def _cookie_domain(self) -> str:
        return urllib.parse.urlsplit(self.base_url).hostname or ""

    def _serialize_cookies(self) -> str:
        # Persist the whole jar (yolo_session + XSRF-TOKEN) so we don't have
        # to hardcode the deployment-specific session cookie name.
        return json.dumps({c.name: c.value for c in self.session.cookies})

    def _install_cookies(self, blob: str) -> None:
        try:
            data = json.loads(blob)
        except (TypeError, ValueError):
            return
        domain = self._cookie_domain()
        for name, value in (data or {}).items():
            self.session.cookies.set(name, value, domain=domain, path="/")

    def _load_cached_session(self) -> bool:
        db_session = get_latest_valid_session(self.backend_name)
        if db_session and db_session.token:
            self._install_cookies(db_session.token)
            self._db_session_id = db_session.id
            self._authenticated = True
            self.logger.debug(
                "yolo: reusing cached session cookies from db session id=%s",
                db_session.id,
            )
            return True
        return False

    def ensure_session(self) -> None:
        if self._authenticated:
            return
        if self._load_cached_session():
            return
        self.login()

    # -- token extraction --------------------------------------------------

    _RE_DCAT_TOKEN = re.compile(r'Dcat\.token\s*=\s*[\'"]([^\'"]+)[\'"]')
    _RE_INPUT_TOKEN = re.compile(r'name="_token"[^>]*value="([^"]+)"')

    @classmethod
    def _extract_token(cls, html: str) -> Optional[str]:
        m = cls._RE_DCAT_TOKEN.search(html)
        if m:
            return m.group(1)
        m = cls._RE_INPUT_TOKEN.search(html)
        return m.group(1) if m else None

    @staticmethod
    def _looks_like_login_page(html: str) -> bool:
        return 'name="username"' in html and "/auth/login" in html

    # -- login -------------------------------------------------------------

    def login(self) -> None:
        """Authenticate and persist the resulting cookie jar.

        Uses a Redis lock so concurrent workers don't stampede; if the lock
        is held by another worker, waits for them to publish a session and
        adopts it.
        """
        if not acquire_login_lock(self.backend_name):
            self.logger.info(
                "yolo: login lock held by another worker; waiting for session."
            )
            session = wait_for_valid_session(
                self.backend_name, self.logger, timeout=40, interval=2,
            )
            if not session or not session.token:
                raise YoloAPIError("Timed out waiting for login from another worker")
            self._install_cookies(session.token)
            self._db_session_id = session.id
            self._authenticated = True
            return

        try:
            invalidate_latest_session(self.backend_name)
            self.session.cookies.clear()
            self._authenticated = False
            self._csrf_token = None
            self._db_session_id = None

            # 1. GET the login page → guest session cookies + login _token
            r = self.session.get(f"{self.base_url}/admin/auth/login", timeout=self.timeout)
            r.raise_for_status()
            login_token = self._extract_token(r.text)
            if not login_token:
                raise YoloAPIError("could not find _token on login page")

            # 2. POST credentials
            rp = self.session.post(
                f"{self.base_url}/admin/auth/login",
                data={
                    "_token": login_token,
                    "username": self.username,
                    "password": self.password,
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-TOKEN": login_token,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                allow_redirects=False,
                timeout=self.timeout,
            )

            ok = False
            try:
                body = rp.json()
                ok = bool(body.get("status")) is True
            except ValueError:
                ok = False
            if not ok:
                raise YoloAPIError(
                    f"login failed (HTTP {rp.status_code}): {rp.text[:200]}"
                )

            # Laravel regenerates the session on login; the authenticated
            # cookie jar is what we persist.
            self._authenticated = True
            self._db_session_id = self._persist_session()
            self.logger.info(
                "yolo: login successful, persisted session id=%s", self._db_session_id,
            )
        finally:
            release_login_lock(self.backend_name)

    def _persist_session(self) -> Optional[int]:
        db_session = create_backend_session(
            backend=self.backend_name,
            token=self._serialize_cookies(),
            expires=None,  # Laravel idle-expiry is unknown; rely on dead-session detection.
            is_valid=True,
        )
        return db_session.id

    # -- CSRF --------------------------------------------------------------

    def _ensure_csrf(self) -> str:
        """Fetch (and cache) the per-session Dcat CSRF token. Triggers a
        re-login if the admin page comes back unauthenticated."""
        if self._csrf_token:
            return self._csrf_token
        r = self._request("GET", "/admin/player_list")
        token = self._extract_token(r.text)
        if not token:
            raise YoloAPIError("could not extract Dcat.token from admin page")
        self._csrf_token = token
        return token

    # -- core request ------------------------------------------------------

    def _is_session_dead(self, resp: requests.Response) -> bool:
        """Detect an expired/missing Laravel session.

        - 401 Unauthenticated (writes send Accept: application/json so the
          auth middleware returns 401 JSON rather than redirecting).
        - 302 redirect to /admin/auth/login (reads).
        - The login form returned in the body in place of the admin page.
        """
        if resp.status_code == 401:
            return True
        if resp.status_code in (301, 302):
            loc = resp.headers.get("Location", "")
            if "/auth/login" in loc:
                return True
        if resp.status_code == 200 and self._looks_like_login_page(resp.text or ""):
            return True
        return False

    def _request(
        self,
        method: str,
        path: str,
        *,
        _retry_auth: bool = True,
        **kwargs,
    ) -> requests.Response:
        """Authenticated request with transparent re-login on session-dead."""
        self.ensure_session()
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", False)
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"

        self.logger.debug("yolo api -> %s %s", method, path)
        resp = self.session.request(method, url, **kwargs)

        if self._is_session_dead(resp) and _retry_auth:
            self.logger.warning(
                "yolo: session appears dead on %s (HTTP %s); re-logging in and retrying once.",
                path, resp.status_code,
            )
            invalidate_latest_session(self.backend_name)
            self._authenticated = False
            self._csrf_token = None
            self._db_session_id = None
            self.session.cookies.clear()
            self.login()
            return self._request(method, path, _retry_auth=False, **kwargs)

        return resp

    def _post_form(self, path: str, data: Dict[str, Any]) -> requests.Response:
        """POST an authenticated, CSRF-bearing form. On a 419 (CSRF expired)
        it refreshes the token once and retries."""
        token = self._ensure_csrf()
        body = dict(data)
        body["_token"] = token
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": token,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        resp = self._request("POST", path, data=body, headers=headers)
        if resp.status_code == 419:
            self.logger.warning("yolo: CSRF expired (419) on %s; refreshing token and retrying.", path)
            self._csrf_token = None
            token = self._ensure_csrf()
            body["_token"] = token
            headers["X-CSRF-TOKEN"] = token
            resp = self._request("POST", path, data=body, headers=headers)
        return resp

    # -- response envelope helpers ----------------------------------------

    @staticmethod
    def _parse_envelope(resp: requests.Response) -> Tuple[bool, str, Dict[str, Any]]:
        """Return (success, message, raw) for a Dcat action / store response.

        Handles all three envelopes (spec §7):
          - 200 {"status":true,"data":{"message":...}}      → success
          - 200 {"status":false,"data":{"message":...}}     → business error
          - 422 {"status":false,"errors":{field:[msg,...]}} → validation error
        """
        try:
            body = resp.json()
        except ValueError:
            return False, f"non-json response (HTTP {resp.status_code}): {resp.text[:160]}", {}

        status = bool(body.get("status"))
        if status:
            data = body.get("data") or {}
            msg = data.get("message", "success") if isinstance(data, dict) else "success"
            return True, str(msg), body

        # failure: prefer validation errors, then business message
        errors = body.get("errors")
        if errors and isinstance(errors, dict):
            flat: List[str] = []
            for field, msgs in errors.items():
                if isinstance(msgs, list):
                    flat.extend(f"{field}: {m}" for m in msgs)
                else:
                    flat.append(f"{field}: {msgs}")
            return False, "; ".join(flat), body

        data = body.get("data")
        if isinstance(data, dict) and data.get("message"):
            return False, str(data["message"]), body
        return False, f"operation failed (HTTP {resp.status_code})", body

    # -- operations --------------------------------------------------------

    def agent_score(self) -> str:
        """GET /admin/refresh_score → the agent's available balance as a
        plain-text number (spec §2)."""
        r = self._request(
            "GET", "/admin/refresh_score",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if r.status_code >= 400:
            raise YoloAPIError(f"refresh_score failed: HTTP {r.status_code}")
        return (r.text or "").strip()

    # Grid row: split tbody into <tr>, each into <td>. Copyable cells carry
    # data-content="<value>"; numeric cells are plain text.
    _RE_TBODY = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.S)
    _RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
    _RE_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    _RE_DATA_CONTENT = re.compile(r'data-content="([^"]*)"')
    _RE_TAGS = re.compile(r"<[^>]+>")

    @classmethod
    def _cell_value(cls, cell_html: str) -> str:
        m = cls._RE_DATA_CONTENT.search(cell_html)
        if m:
            return unescape(m.group(1)).strip()
        return unescape(cls._RE_TAGS.sub(" ", cell_html)).strip()

    # Paginated by-account search bounds. The grid returns ~10 rows/page; the
    # exact match can be buried behind longer-named siblings on a later page.
    _SEARCH_MAX_PAGES = 15

    def _parse_grid_rows(self, html: str) -> List[Tuple[str, str, str]]:
        """Return [(player_id, account, score), ...] for each result row.

        Also opportunistically caches the per-session CSRF token exposed by
        the page.
        """
        if not self._csrf_token:
            tok = self._extract_token(html)
            if tok:
                self._csrf_token = tok

        tbody = self._RE_TBODY.search(html)
        if not tbody:
            return []
        out: List[Tuple[str, str, str]] = []
        for row_html in self._RE_TR.findall(tbody.group(1)):
            cells = self._RE_TD.findall(row_html)
            if len(cells) <= _COL_PLAYER_SCORE:
                continue
            out.append((
                self._cell_value(cells[_COL_PLAYER_ID]),
                self._cell_value(cells[_COL_ACCOUNT]),
                self._cell_value(cells[_COL_PLAYER_SCORE]),
            ))
        return out

    def find_by_game_id(self, game_id) -> Optional[Tuple[str, str]]:
        """Look a player up by Player ID via the exact UserID grid filter
        (spec §3), which returns at most one row. Returns (player_id, score)
        or None.

        This is the robust primary lookup: the by-account search is a partial
        match, so a short username (e.g. `userYL4`) is buried behind every
        `userYL4*` account and can fall on a later page. The by-ID filter has
        no such ambiguity.
        """
        gid = str(game_id).strip()
        if not gid:
            return None
        r = self._request("GET", "/admin/player_list", params={"UserID": gid})
        if r.status_code >= 400:
            raise YoloAPIError(f"player_list failed: HTTP {r.status_code}")
        for pid, _account, score in self._parse_grid_rows(r.text):
            if pid == gid:
                return pid, score
        return None

    def search_user(self, account: str) -> Optional[Tuple[str, str]]:
        """Paginated by-account search; returns (player_id, score) for the row
        whose Account equals `account` exactly, or None.

        The grid Accounts filter is a partial match, so this walks pages until
        it finds the exact match or runs out of results. Prefer find_by_game_id
        when the Player ID is known.
        """
        target = account.strip()
        for page in range(1, self._SEARCH_MAX_PAGES + 1):
            r = self._request(
                "GET", "/admin/player_list", params={"Accounts": account, "page": page},
            )
            if r.status_code >= 400:
                raise YoloAPIError(f"player_list failed: HTTP {r.status_code}")
            rows = self._parse_grid_rows(r.text)
            for pid, acct, score in rows:
                if acct == target:
                    return pid, score
            if not rows:
                break  # past the last page
        return None

    def find_player(self, account: str, game_id=None) -> Tuple[str, str]:
        """Resolve (player_id, score), preferring the exact by-ID lookup when
        the Player ID is known and falling back to the paginated by-account
        search."""
        if game_id:
            hit = self.find_by_game_id(game_id)
            if hit:
                return hit
        hit = self.search_user(account)
        if hit:
            return hit
        raise YoloAPIError(
            f"no exact-match player for account={account!r} "
            f"(searched by game_id={game_id!r} and paginated account search)"
        )

    def _user_recharge(
        self, *, player_id: str, account: str, score: str, op_type: int,
        amount: int, remark: str,
    ) -> Tuple[bool, str]:
        current = f"{self.base_url}/admin/player_list?"
        payload = {
            "_current_": current,
            "UserID": str(player_id),
            "Accounts": account,
            "Score": str(score),
            "type": str(op_type),
            "renderable": "App_Admin_Actions_UserRecharge",
            "_trans_": "user",
        }
        body = {
            "UserID": str(player_id),
            "Accounts": account,
            "Score": str(score),
            "type": str(op_type),
            "input_score": str(amount),
            "remark": remark,
            "_form_": _RECHARGE_FORM,
            "_current_": current,
            "_payload_": json.dumps(payload),
        }
        resp = self._post_form("/admin/dcat-api/form", body)
        success, message, _ = self._parse_envelope(resp)
        return success, message

    def recharge(self, *, player_id: str, account: str, score: str, amount: int, remark: str = "") -> Tuple[bool, str]:
        """Add credit to a player (type=1): debits the agent (spec §4)."""
        return self._user_recharge(
            player_id=player_id, account=account, score=score,
            op_type=TYPE_RECHARGE, amount=amount, remark=remark,
        )

    def redeem(self, *, player_id: str, account: str, score: str, amount: int, remark: str = "") -> Tuple[bool, str]:
        """Remove credit from a player (type=2): credits the agent (spec §4).

        The system's external name is 'withdraw'; YOLO labels it Redeem.
        """
        return self._user_recharge(
            player_id=player_id, account=account, score=score,
            op_type=TYPE_REDEEM, amount=amount, remark=remark,
        )

    def reset_password(self, *, player_id: str, account: str, new_password: str) -> Tuple[bool, str]:
        """Reset a player's login password (spec §5)."""
        current = f"{self.base_url}/admin/player_list?"
        payload = {
            "_current_": current,
            "userid": str(player_id),
            "username": account,
            "renderable": "App_Admin_Actions_ResetUserPass",
            "_trans_": "user",
        }
        body = {
            "UserID": str(player_id),
            "Accounts": account,
            "password": new_password,
            "_form_": _RESET_FORM,
            "_current_": current,
            "_payload_": json.dumps(payload),
        }
        resp = self._post_form("/admin/dcat-api/form", body)
        success, message, _ = self._parse_envelope(resp)
        return success, message

    def create_player(self, *, account: str, password: str, nickname: str = "", recharge_amount: int = 0) -> Tuple[bool, str]:
        """Create a new player (spec §6). Returns (success, message).

        On success `message` is an HTML snippet containing the new account +
        password; on a validation failure it's the flattened error list.
        """
        token = self._ensure_csrf()
        body = {
            "Accounts": account,
            "NickName": nickname or account,
            "Recharge_Amount": str(recharge_amount),
            "LogonPass": password,
            "RegisterIP": self.register_ip,
            # Hidden/default fields the UI sends; the server overwrites most.
            "ChannelID": "",
            "RegAccounts": "",
            "AgentID": "",
            "InsurePass": "",
            "FaceID": "",
            "LastLogonIP": self.register_ip,
            "MemberOrder": "",
            "MemberExp": "",
            "RegisterMobile": "",
            "RegisterMachine": "",
            "BindAgentDate": "",
            "Nullity": "1",
            "_previous_": f"{self.base_url}/admin/player_list",
            "_token": token,
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": token,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        resp = self._request("POST", "/admin/player_list", data=body, headers=headers)
        if resp.status_code == 419:
            self.logger.warning("yolo: CSRF expired (419) on create; refreshing and retrying.")
            self._csrf_token = None
            token = self._ensure_csrf()
            body["_token"] = token
            headers["X-CSRF-TOKEN"] = token
            resp = self._request("POST", "/admin/player_list", data=body, headers=headers)
        success, message, _ = self._parse_envelope(resp)
        return success, message


def build_client_from_backend(backend, logger: Optional[logging.Logger] = None) -> YoloClient:
    """Construct a YoloClient from a BackendGame row.

    Pulls credentials and base URL from DB columns when present and falls
    back to the backend's config constants.
    """
    from backends.yolo.config import (
        API_BASE_URL,
        BACKEND_NAME,
        DEFAULT_REGISTER_IP,
        PASSWORD,
        USERNAME,
    )

    return YoloClient(
        base_url=backend.api_base_url or API_BASE_URL,
        username=backend.username or USERNAME,
        password=backend.password or PASSWORD,
        backend_name=backend.name or BACKEND_NAME,
        register_ip=DEFAULT_REGISTER_IP,
        logger=logger,
    )
