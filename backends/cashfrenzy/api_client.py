"""
CashFrenzy agent API client.

A pure-HTTP client for the CashFrenzy agent backend at
agentserver.cashfrenzy777.com. It is the same vendor software as gameroom
(Laravel + stateless JWT), so this mirrors GameroomClient. Spec:

    /Applications/development/cashfrenzy-standalone/api_findings.md

Authentication model (verified live):
    - POST /api/login → JWT bearer (~6h lifetime); response carries
      top-level `token`, `expires_time` (unix epoch) and `money`.
    - All other endpoints accept Bearer + form-urlencoded body.
    - Server returns HTTP 200 with a custom `status_code` in the body — branch
      on `status_code`, not the HTTP status nor the inconsistent `code` field.
    - A dead/expired token surfaces as status_code 401/410; re-login + retry once.

Session persistence:
    The JWT is cached in the `backend_sessions` table so it survives across
    workers/process restarts. A Redis login lock prevents a thundering herd
    when several workers discover an expired token at once.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

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


class CashFrenzyAPIError(Exception):
    def __init__(self, status_code: int, message: str, code: Optional[int] = None):
        super().__init__(f"cashfrenzy api error (status_code={status_code}): {message}")
        self.status_code = status_code
        self.message = message
        self.code = code


_OK = 200
# Token dead / unauthenticated — re-login and retry once.
_TOKEN_DEAD_CODES = {401, 410}

# userList search-field discriminator (spec §5): 1 = Account, 2 = ID, 3 = Manager.
# NOTE: by-ID (type=2) filtering is ignored server-side on this deployment, so
# the automation resolves players by the by-account search (type=1) instead.
SEARCH_BY_ACCOUNT = 1


class CashFrenzyClient:
    DEFAULT_TIMEOUT = 30
    _EXPIRY_GUARD_SECS = 60
    _LOGIN_WAIT_TIMEOUT = 40
    _LOGIN_WAIT_INTERVAL = 2

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        backend_name: str,
        logger: Optional[logging.Logger] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not base_url or not username or not password:
            raise ValueError("CashFrenzyClient requires base_url, username, and password.")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.backend_name = backend_name
        self.timeout = timeout
        self.logger = logger or logging.getLogger("casino_automation.cashfrenzy.api")

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
        # The host intermittently drops keep-alive connections; don't reuse them.
        self.session.headers.update({"Connection": "close"})

        self._token: Optional[str] = None
        self._expires_at: int = 0
        self._db_session_id: Optional[int] = None
        # Agent `money` captured from the most recent /api/login response, used
        # as a fallback if /api/agent/getMoney parsing fails.
        self.last_login_money: Optional[Any] = None

    @property
    def db_session_id(self) -> Optional[int]:
        return self._db_session_id

    # -- auth --------------------------------------------------------------

    def ensure_token(self) -> None:
        """Make sure self._token is fresh; load from DB or login as needed."""
        if self._token and time.time() < self._expires_at - self._EXPIRY_GUARD_SECS:
            return

        db_session = get_latest_valid_session(self.backend_name)
        if db_session and db_session.token and db_session.expires:
            try:
                exp = int(db_session.expires)
            except (TypeError, ValueError):
                exp = 0
            if exp and time.time() < exp - self._EXPIRY_GUARD_SECS:
                self._token = db_session.token
                self._expires_at = exp
                self._db_session_id = db_session.id
                self.logger.debug(
                    "cashfrenzy: reusing cached JWT session id=%s (exp=%s)",
                    db_session.id, exp,
                )
                return

        self._login()

    def _login(self) -> None:
        """Login under Redis lock; persist token to backend_sessions.

        If another worker holds the lock, wait for them to publish a session
        and adopt it instead of double-logging.
        """
        if not acquire_login_lock(self.backend_name):
            self.logger.info("cashfrenzy: login lock held by another worker; waiting for session.")
            session = wait_for_valid_session(
                self.backend_name, self.logger,
                timeout=self._LOGIN_WAIT_TIMEOUT, interval=self._LOGIN_WAIT_INTERVAL,
            )
            if not session or not session.token:
                raise CashFrenzyAPIError(0, "Timed out waiting for login from another worker")
            self._token = session.token
            try:
                self._expires_at = int(session.expires)
            except (TypeError, ValueError):
                self._expires_at = int(time.time()) + 3600
            self._db_session_id = session.id
            return

        try:
            invalidate_latest_session(self.backend_name)

            resp = self.session.post(
                f"{self.base_url}/api/login",
                data={"username": self.username, "password": self.password, "captcha": "0000"},
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            sc = int(body.get("status_code", -1))
            if sc != _OK:
                raise CashFrenzyAPIError(sc, str(body.get("message", "login failed")))

            token = body.get("token")
            expires_time = body.get("expires_time")
            if not token or not expires_time:
                raise CashFrenzyAPIError(sc, "login response missing token/expires_time")

            self._token = str(token)
            self._expires_at = int(expires_time)
            self.last_login_money = body.get("money")
            db_session = create_backend_session(
                backend=self.backend_name,
                token=self._token,
                expires=str(self._expires_at),
                is_valid=True,
            )
            self._db_session_id = db_session.id
            self.logger.info(
                "cashfrenzy: login successful, persisted session id=%s (exp=%s)",
                db_session.id, self._expires_at,
            )
        finally:
            release_login_lock(self.backend_name)

    # -- core request ------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        _retry_on_auth: bool = True,
    ) -> Tuple[int, str, Dict[str, Any]]:
        self.ensure_token()

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        form: Optional[Dict[str, str]] = None
        if data is not None:
            form = {k: ("" if v is None else str(v)) for k, v in data.items()}

        self.logger.debug("cashfrenzy api -> %s %s", method, path)
        resp = self.session.request(
            method, url, params=params, data=form, headers=headers, timeout=self.timeout,
        )

        try:
            body = resp.json()
        except ValueError:
            raise CashFrenzyAPIError(
                resp.status_code, f"non-json response from {path}: {resp.text[:200]}",
            )

        sc = int(body.get("status_code", -1))
        msg = str(body.get("message", ""))
        code = body.get("code")
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None

        self.logger.debug(
            "cashfrenzy api <- %s status_code=%s code=%s msg=%s", path, sc, code, msg,
        )

        if sc in _TOKEN_DEAD_CODES and _retry_on_auth:
            self.logger.warning(
                "cashfrenzy: status_code=%s on %s; re-logging in and retrying once.", sc, path,
            )
            invalidate_latest_session(self.backend_name)
            self._token = None
            self._expires_at = 0
            self._db_session_id = None
            self._login()
            return self._request(method, path, params=params, data=data, _retry_on_auth=False)

        return sc, msg, body

    # -- typed endpoints ---------------------------------------------------

    def agent_balance(self) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/agent/getMoney → data is the agent balance string."""
        return self._request("POST", "/api/agent/getMoney")

    def user_list(
        self,
        *,
        search_content: Optional[str] = None,
        search_type: int = SEARCH_BY_ACCOUNT,
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[int, str, Dict[str, Any]]:
        """GET /api/player/userList. With search_content set, filters by
        search_type (1 = Account)."""
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if search_content:
            params["search_type"] = search_type
            params["search_content"] = search_content
        return self._request("GET", "/api/player/userList", params=params)

    def agent_money(self, player_id: Any) -> Tuple[int, str, Dict[str, Any]]:
        """GET /api/player/agentMoney?id=<gameId> → data.balance (player),
        data.cusBlance (agent)."""
        return self._request("GET", "/api/player/agentMoney", params={"id": str(player_id)})

    def player_insert(
        self,
        *,
        username: str,
        password: str,
        money: int = 0,
        nickname: Optional[str] = None,
    ) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/player/playerInsert."""
        data = {
            "username": username,
            "nickname": nickname or username,
            "money": money,
            "password": password,
            "password_confirmation": password,
        }
        return self._request("POST", "/api/player/playerInsert", data=data)

    def agent_recharge(
        self,
        *,
        player_id: Any,
        balance: int,
        available_balance: Any,
        remark: str = "",
    ) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/player/agentRecharge (opera_type=0) — debits agent, credits player."""
        data = {
            "id": str(player_id),
            "available_balance": str(available_balance),
            "opera_type": 0,
            "balance": balance,
            "remark": remark,
        }
        return self._request("POST", "/api/player/agentRecharge", data=data)

    def agent_withdraw(
        self,
        *,
        player_id: Any,
        balance: int,
        customer_balance: Any,
        remark: str = "",
    ) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/player/agentWithdraw (opera_type=1) — credits agent, debits player."""
        data = {
            "id": str(player_id),
            "customer_balance": str(customer_balance),
            "opera_type": 1,
            "balance": balance,
            "remark": remark,
        }
        return self._request("POST", "/api/player/agentWithdraw", data=data)

    def reset_password(
        self,
        *,
        player_id: Any,
        password: str,
    ) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/player/reset — letters+numbers, 6-16 chars."""
        data = {
            "id": str(player_id),
            "password": password,
            "password_confirmation": password,
        }
        return self._request("POST", "/api/player/reset", data=data)


def build_client_from_backend(backend, logger: Optional[logging.Logger] = None) -> CashFrenzyClient:
    """Construct a CashFrenzyClient from a BackendGame row.

    Prefers DB-seeded values; falls back to config constants if absent.
    """
    from backends.cashfrenzy.config import API_BASE_URL, USERNAME, PASSWORD, BACKEND_NAME

    return CashFrenzyClient(
        base_url=backend.api_base_url or API_BASE_URL,
        username=backend.username or USERNAME,
        password=backend.password or PASSWORD,
        backend_name=backend.name or BACKEND_NAME,
        logger=logger,
    )
