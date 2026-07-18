"""
Cash Machine 777 agent API client.

Cash Machine 777 is the same white-label "layui" agent panel as gameroom and
exposes the same JWT + form-urlencoded JSON API. This client is a direct port
of backends/gameroom/api_client.py; differences confirmed by live recon are
noted inline. The full endpoint spec + verification log lives at:

    ./cash_machine_777_api_findings.md

Authentication model (verified live 2026-06-25):
    - POST /api/login → JWT bearer (~6h lifetime), no captcha required.
    - All other endpoints accept Bearer + form-urlencoded body.
    - Server returns HTTP 200 with a custom `status_code` in the body — always
      branch on `status_code`, not HTTP status nor the inconsistent `code` field.
    - `status_code == 410` ("Please login again") means the JWT is dead;
      transparently re-login and retry once.

Session persistence:
    The JWT is cached in the existing `backend_sessions` table so it survives
    across workers and process restarts. Redis-backed `acquire_login_lock`
    prevents a thundering herd when several workers discover an expired token
    simultaneously.
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


class CashMachineAPIError(Exception):
    def __init__(self, status_code: int, message: str, code: Optional[int] = None):
        super().__init__(
            f"cashmachine api error (status_code={status_code}): {message}"
        )
        self.status_code = status_code
        self.message = message
        self.code = code


# Server "OK" envelope is always status_code == 200. Everything else is an
# error whose user-facing reason is in `message` (already localized).
_OK = 200
_TOKEN_DEAD = 410


class CashMachineClient:
    DEFAULT_TIMEOUT = 30
    # Re-login when the cached token has <60s remaining to avoid mid-flight 410.
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
            raise ValueError(
                "CashMachineClient requires base_url, username, and password."
            )
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.backend_name = backend_name
        self.timeout = timeout
        self.logger = logger or logging.getLogger(
            "casino_automation.cashmachine.api"
        )

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

        self._token: Optional[str] = None
        self._expires_at: int = 0
        self._db_session_id: Optional[int] = None
        # Agent `money` field captured from the most recent /api/login response.
        # Used as a fallback for /api/agent/getMoney (see _extract_agent_money).
        self.last_login_money: Optional[Any] = None

    # -- session id used by callers for active_tasks_count bookkeeping -----

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
                    "cashmachine: reusing cached JWT session id=%s (exp=%s)",
                    db_session.id, exp,
                )
                return

        self._login()

    def _login(self) -> None:
        """Login under Redis lock; persist token to backend_sessions table.

        If another worker holds the lock, wait for them to publish a session
        and adopt it instead of double-logging.
        """
        if not acquire_login_lock(self.backend_name):
            self.logger.info(
                "cashmachine: login lock held by another worker; waiting for session."
            )
            session = wait_for_valid_session(
                self.backend_name, self.logger,
                timeout=self._LOGIN_WAIT_TIMEOUT,
                interval=self._LOGIN_WAIT_INTERVAL,
            )
            if not session or not session.token:
                raise CashMachineAPIError(
                    0, "Timed out waiting for login from another worker"
                )
            self._token = session.token
            try:
                self._expires_at = int(session.expires)
            except (TypeError, ValueError):
                self._expires_at = int(time.time()) + 3600
            self._db_session_id = session.id
            return

        try:
            # Drop any stale row before writing the new one.
            invalidate_latest_session(self.backend_name)

            url = f"{self.base_url}/api/login"
            resp = self.session.post(
                url,
                data={"username": self.username, "password": self.password},
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            sc = int(body.get("status_code", -1))
            if sc != _OK:
                raise CashMachineAPIError(sc, str(body.get("message", "login failed")))

            token = body.get("token")
            expires_time = body.get("expires_time")
            if not token or not expires_time:
                raise CashMachineAPIError(
                    sc, "login response missing token/expires_time"
                )

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
                "cashmachine: login successful, persisted session id=%s (exp=%s)",
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
        _retry_on_410: bool = True,
    ) -> Tuple[int, str, Dict[str, Any]]:
        self.ensure_token()

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        # Stringify form values so requests urlencodes consistently.
        form: Optional[Dict[str, str]] = None
        if data is not None:
            form = {k: ("" if v is None else str(v)) for k, v in data.items()}

        self.logger.debug("cashmachine api -> %s %s", method, path)
        resp = self.session.request(
            method,
            url,
            params=params,
            data=form,
            headers=headers,
            timeout=self.timeout,
        )

        try:
            body = resp.json()
        except ValueError:
            raise CashMachineAPIError(
                resp.status_code,
                f"non-json response from {path}: {resp.text[:200]}",
            )

        sc = int(body.get("status_code", -1))
        msg = str(body.get("message", ""))
        code = body.get("code")
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None

        self.logger.debug(
            "cashmachine api <- %s status_code=%s code=%s msg=%s",
            path, sc, code, msg,
        )

        if sc == _TOKEN_DEAD and _retry_on_410:
            self.logger.warning(
                "cashmachine: status_code=410 on %s; re-logging in and retrying once.",
                path,
            )
            invalidate_latest_session(self.backend_name)
            self._token = None
            self._expires_at = 0
            self._db_session_id = None
            self._login()
            return self._request(method, path, params=params, data=data, _retry_on_410=False)

        return sc, msg, body

    # -- typed endpoints ---------------------------------------------------

    def agent_balance(self) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/agent/getMoney.

        Verified CM777 shape: {status_code:200, message, data:"0.00"} — the
        agent balance is returned directly as `data` (a string), NOT nested
        under money/balance/cusBlance. See _extract_agent_money in automation.
        """
        return self._request("POST", "/api/agent/getMoney")

    def user_list(
        self,
        *,
        account: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[int, str, Dict[str, Any]]:
        """GET /api/player/userList[?account=&page=&limit=]."""
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if account:
            params["account"] = account
        return self._request("GET", "/api/player/userList", params=params)

    def agent_money(self, player_id: Any) -> Tuple[int, str, Dict[str, Any]]:
        """GET /api/player/agentMoney?id=<playerId> → 'balance' (player) + 'cusBlance' (agent)."""
        return self._request("GET", "/api/player/agentMoney", params={"id": str(player_id)})

    def player_insert(
        self,
        *,
        username: str,
        password: str,
        money: int = 0,
        nickname: Optional[str] = None,
    ) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/player/playerInsert.

        CM777 username rule (verified): letters + numbers only, 5-20 chars.
        """
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
        bonus: int = 0,
    ) -> Tuple[int, str, Dict[str, Any]]:
        """POST /api/player/agentRecharge — debits agent, credits player."""
        data = {
            "id": str(player_id),
            "available_balance": str(available_balance),
            "opera_type": 0,
            "bonus": bonus,
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
        """POST /api/player/agentWithdraw — credits agent, debits player.

        External name is "withdraw"; the user-facing label is "Redeem".
        """
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
        """POST /api/player/reset. Reset password rule differs from create
        (must contain upper + lower + symbol)."""
        data = {
            "id": str(player_id),
            "password": password,
            "password_confirmation": password,
        }
        return self._request("POST", "/api/player/reset", data=data)


def build_client_from_backend(
    backend, logger: Optional[logging.Logger] = None
) -> CashMachineClient:
    """Construct a CashMachineClient from a BackendGame row.

    Prefers DB-seeded values; falls back to config constants if absent.
    """
    from backends.cashmachine.config import (
        LOGIN_URL, USERNAME, PASSWORD, BACKEND_NAME, API_BASE_URL,
    )

    base_url = (
        backend.api_base_url
        or _derive_api_base(backend.backend_url or LOGIN_URL)
        or API_BASE_URL
    )
    return CashMachineClient(
        base_url=base_url,
        username=backend.username or USERNAME,
        password=backend.password or PASSWORD,
        backend_name=backend.name or BACKEND_NAME,
        logger=logger,
    )


def _derive_api_base(admin_url: str) -> str:
    """Strip any path (e.g. /admin/login) to recover the bare API host."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(admin_url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
